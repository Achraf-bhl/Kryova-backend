"""What the machine resists being turned by — the inertia half of the mass budget.

`app.assembly.mass` answers what an assembly weighs and where its centre of mass is.
This module answers the next question, which is the one a joint *moment* needs: how the
mass is distributed about that centre. Until this existed every body reached
`app.dynamics` as a point mass, `reactions.py` marked every joint moment approximated,
and moments were the one quantity neither dynamics engine had ever had checked against a
closed form (master plan E9, and THE QUEUE G6).

It is a **separate module from `mass.py` rather than more fields on `MassRollup`**, and
the reason is the word *complete*. A roll-up is complete when nothing was left out, and
inertia can fail where mass succeeds: a component measured at `Detail.FULL` has a mass
and no tensor at all, because `Detail.INERTIA` is a *higher* level than `FULL` and is
never computed speculatively. One `complete` covering both would be false for a perfectly
good mass budget, or true for an inertia roll-up missing half its tensors. So there are
two roll-ups, they share the `ComponentMeasurer` seam and the "every omission is named"
discipline, and each says what it alone knows.

Four facts, each measured against the real kernel on 2026-09-16 rather than recalled,
because each one gives a plausible wrong moment rather than an error:

1. **OCCT's `MatrixOfInertia()` is about the centre of mass**, in axes parallel to the
   global ones — not about the origin, and not in the part's own principal axes. A
   60x40x20 box reports exactly `V(b^2 + c^2)/12` on the diagonal, which is the centroidal
   value; the corner value `V(b^2 + c^2)/3` is four times larger, so the two cannot be
   confused once either is measured.
2. **Its off-diagonal entries are the NEGATED products of inertia** — `-∫xy dV`, the
   inertia-tensor convention, not `+∫xy dV`. An L-shaped plate whose analytic
   `∫(x-xc)(y-yc) dV` is `-2.0833e7 mm^5` reports `tensor[0][1] = +2.0833e7`. Take the
   other convention and every product of inertia is sign-flipped at exactly the right
   magnitude, which no magnitude check can see. `_shift` below uses the same convention
   and is held to it by the assembly oracle in the tests.
3. **The kernel's tensor is a second moment of VOLUME, in mm^5**, with density
   deliberately not folded in (`metrology.inertia_tensor_mm5` says so). The conversion to
   kg.mm^2 here is `mass_kg / volume_mm3` — the component's own density, read off the
   payload it already carries. That is not a shortcut around the units rule, it is the
   way to obey it: a literal `1e-9` for kg/m^3 to kg/mm^3 would be a unit constant that
   nothing checks, while a density derived from two numbers in the same payload is
   automatically consistent with the mass being summed, and stays right for an assembly
   whose components are different materials.
4. **The parallel-axis theorem assembles exactly.** Two boxes shifted onto a common
   centre reproduce the tensor OCCT measures on the shape they fuse into, to 1e-16 of the
   tensor's own magnitude. That is the oracle this module is verified against, and it has
   teeth: flipping the sign in the `m*dx*dy` term moves `xy` from `+2.08e7` to `-2.08e7`.

Units: mass kg, lengths mm, inertia **kg.mm^2** — the same spelling
`app.dynamics.types.Body.inertia_kg_mm2` uses, so nothing converts downstream.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.assembly.mass import ComponentMeasurer, MissingMass
from app.assembly.structure import Occurrence, ProductStructure
from app.dynamics.pose import Rot3, Vec3

#: Payload keys read here. Spelled locally for `mass.py`'s reason — so importing this
#: package does not pull ~166 MB of OCP in behind `app.kernel.__init__` — and asserted
#: equal to `app.kernel.measurement`'s constants by the tests.
INERTIA_TENSOR_MM5: Final = "inertia_tensor_mm5"
VOLUME_MM3: Final = "volume_mm3"
MASS_KG: Final = "mass_kg"
CENTRE_OF_MASS_MM: Final = "centre_of_mass_mm"

#: How large a product of inertia may be, relative to the largest moment, before this
#: module refuses to hand a body the diagonal alone.
#:
#: **A convention, chosen and stated rather than measured**, and the refusal it drives is
#: the point of the number rather than the number itself. The products of inertia are
#: what couple a rotation about one axis into a moment about the other two, so they *are*
#: the bearing loads an engineer is asking for; dropping them under-reports a real moment,
#: which is the direction every check passes. One percent is tight enough that a part
#: which passes is genuinely near-diagonal, and an ordinary L-shaped link — 18% on the
#: plate measured above — is refused rather than quietly halved.
MAX_PRODUCT_FRACTION: Final = 0.01


@dataclass(frozen=True)
class InertiaTensor:
    """A symmetric inertia tensor in kg.mm^2, about a stated point in stated axes.

    Six independent numbers rather than a 3x3, because the symmetry is then structural:
    there is no second copy of `xy` to fall out of step with the first, and no caller can
    build an asymmetric tensor by accident. `as_rows` renders the 3x3 where one is wanted.

    The off-diagonals follow OCCT's convention — `xy` is `-∫xy dm`, not `+∫xy dm` — so a
    tensor read from the kernel needs no sign fix on the way in, and `_shift` uses the
    same convention on the way through.
    """

    xx: float = 0.0
    yy: float = 0.0
    zz: float = 0.0
    xy: float = 0.0
    xz: float = 0.0
    yz: float = 0.0

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[float]]) -> InertiaTensor:
        """Read a 3x3, taking the upper triangle and ignoring the mirrored half."""
        return cls(
            xx=float(rows[0][0]),
            yy=float(rows[1][1]),
            zz=float(rows[2][2]),
            xy=float(rows[0][1]),
            xz=float(rows[0][2]),
            yz=float(rows[1][2]),
        )

    def as_rows(self) -> list[list[float]]:
        return [
            [self.xx, self.xy, self.xz],
            [self.xy, self.yy, self.yz],
            [self.xz, self.yz, self.zz],
        ]

    @property
    def diagonal(self) -> Vec3:
        return (self.xx, self.yy, self.zz)

    @property
    def largest_moment(self) -> float:
        return max(abs(self.xx), abs(self.yy), abs(self.zz))

    @property
    def largest_product(self) -> float:
        return max(abs(self.xy), abs(self.xz), abs(self.yz))

    @property
    def product_fraction(self) -> float:
        """The largest product of inertia as a fraction of the largest moment.

        Zero for a tensor with no products, and zero for a tensor of nothing — a body
        with no moments has no coupling to lose, so the fraction is not `inf`.
        """
        moment = self.largest_moment
        if moment <= 0.0:
            return 0.0
        return self.largest_product / moment

    @property
    def is_near_diagonal(self) -> bool:
        return self.product_fraction <= MAX_PRODUCT_FRACTION

    def scaled(self, factor: float) -> InertiaTensor:
        return InertiaTensor(
            xx=self.xx * factor,
            yy=self.yy * factor,
            zz=self.zz * factor,
            xy=self.xy * factor,
            xz=self.xz * factor,
            yz=self.yz * factor,
        )

    def plus(self, other: InertiaTensor) -> InertiaTensor:
        """Tensors add, but only about a common point — see `InertiaRollup.about`."""
        return InertiaTensor(
            xx=self.xx + other.xx,
            yy=self.yy + other.yy,
            zz=self.zz + other.zz,
            xy=self.xy + other.xy,
            xz=self.xz + other.xz,
            yz=self.yz + other.yz,
        )

    def rotated(self, rotation: Rot3) -> InertiaTensor:
        """This tensor expressed in axes rotated by `rotation` — `R I R^T`.

        `Rot3` is row-major, matching `app.dynamics.pose.apply`.
        """
        r = rotation
        rows = self.as_rows()
        # (R I)[i][k] = sum_j R[i][j] I[j][k]
        ri = [
            [sum(r[i * 3 + j] * rows[j][k] for j in range(3)) for k in range(3)]
            for i in range(3)
        ]
        # (R I R^T)[i][k] = sum_j (R I)[i][j] R[k][j]
        out = [
            [sum(ri[i][j] * r[k * 3 + j] for j in range(3)) for k in range(3)]
            for i in range(3)
        ]
        return InertiaTensor.from_rows(out)

    def to_dict(self) -> dict[str, float]:
        return {
            "xx": self.xx,
            "yy": self.yy,
            "zz": self.zz,
            "xy": self.xy,
            "xz": self.xz,
            "yz": self.yz,
        }


def _shift(tensor: InertiaTensor, mass_kg: float, offset_mm: Vec3) -> InertiaTensor:
    """The parallel-axis theorem: a centroidal tensor about a point `offset` away.

    `I = I_c + m(|d|^2 E - d (x) d)`, which in OCCT's sign convention is a **plus** on the
    diagonal and a **minus** on the products. Getting the product sign wrong leaves every
    off-diagonal at exactly the right magnitude with the wrong sign, so it is checked by
    assembling two pieces and comparing against the monolith rather than by inspection.
    """
    dx, dy, dz = offset_mm
    return InertiaTensor(
        xx=tensor.xx + mass_kg * (dy * dy + dz * dz),
        yy=tensor.yy + mass_kg * (dx * dx + dz * dz),
        zz=tensor.zz + mass_kg * (dx * dx + dy * dy),
        xy=tensor.xy - mass_kg * dx * dy,
        xz=tensor.xz - mass_kg * dx * dz,
        yz=tensor.yz - mass_kg * dy * dz,
    )


@dataclass(frozen=True)
class PlacedInertia:
    """One occurrence's tensor, about **its own** centre of mass, in world axes.

    Kept about its own centre rather than pre-shifted to the assembly's, because a body
    is a sub-tree and every sub-tree has a different centre. Shifting once per query is
    six multiplications; shifting on the way in would mean storing one of these per
    question anybody might ask.
    """

    path: str
    component: str
    mass_kg: float
    centre_of_mass_mm: Vec3
    tensor_kg_mm2: InertiaTensor

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "component": self.component,
            "mass_kg": self.mass_kg,
            "centre_of_mass_mm": list(self.centre_of_mass_mm),
            "inertia_kg_mm2": self.tensor_kg_mm2.to_dict(),
        }


@dataclass(frozen=True)
class InertiaRollup:
    """Every occurrence's inertia, and every occurrence whose inertia is missing.

    Truthy only when `complete`, for `MassRollup`'s reason: a tensor summed over part of
    a machine is not that machine's tensor, it is a smaller machine's, and it is smaller
    in the direction that makes a moment check pass.
    """

    weighed: tuple[PlacedInertia, ...] = ()
    missing: tuple[MissingMass, ...] = ()
    occurrences: int = 0
    components_measured: int = 0

    @property
    def complete(self) -> bool:
        return not self.missing and bool(self.weighed)

    def __bool__(self) -> bool:
        return self.complete

    @property
    def mass_kg(self) -> float:
        return sum(item.mass_kg for item in self.weighed)

    @property
    def centre_of_mass_mm(self) -> Vec3 | None:
        """The mass-weighted centre of what was measured, or `None` if it has no mass."""
        total = self.mass_kg
        if not self.weighed or total <= 0.0:
            return None
        return (
            sum(i.mass_kg * i.centre_of_mass_mm[0] for i in self.weighed) / total,
            sum(i.mass_kg * i.centre_of_mass_mm[1] for i in self.weighed) / total,
            sum(i.mass_kg * i.centre_of_mass_mm[2] for i in self.weighed) / total,
        )

    def about(self, point_mm: Vec3) -> InertiaTensor:
        """The tensor of everything measured, about `point_mm`, in world axes.

        Each occurrence is shifted from its own centre to the common point and the
        shifted tensors are summed. Tensors add only about a common point, which is why
        this is a method taking a point rather than a property.
        """
        total = InertiaTensor()
        for item in self.weighed:
            offset = (
                item.centre_of_mass_mm[0] - point_mm[0],
                item.centre_of_mass_mm[1] - point_mm[1],
                item.centre_of_mass_mm[2] - point_mm[2],
            )
            total = total.plus(_shift(item.tensor_kg_mm2, item.mass_kg, offset))
        return total

    def about_centre(self) -> InertiaTensor | None:
        """The tensor about the measured centre of mass — `None` when there is none."""
        centre = self.centre_of_mass_mm
        return None if centre is None else self.about(centre)

    def under(self, path: str) -> InertiaRollup:
        """The sub-roll-up for one body: every occurrence at or beneath `path`.

        Carries that body's *own* missing entries, so a body whose sub-tree is complete
        is usable even when another body in the same machine is not. `occurrences` and
        `components_measured` are recounted over the subset rather than inherited, or a
        sub-roll-up would report the whole machine's totals.
        """
        weighed = tuple(i for i in self.weighed if _at_or_under(i.path, path))
        missing = tuple(m for m in self.missing if _at_or_under(m.path, path))
        return InertiaRollup(
            weighed=weighed,
            missing=missing,
            occurrences=len(weighed) + len(missing),
            components_measured=len({i.component for i in weighed}),
        )

    def summary(self) -> str:
        if not self.weighed and not self.missing:
            return "Nothing to measure."
        head = (
            f"{len(self.weighed)} of {self.occurrences} occurrences carry an inertia "
            f"tensor ({self.components_measured} components measured)"
        )
        tensor = self.about_centre()
        if tensor is not None:
            d = tensor.diagonal
            head += (
                f"; about the centre of mass the diagonal is "
                f"({d[0]:.4g}, {d[1]:.4g}, {d[2]:.4g}) kg.mm2 with the largest product "
                f"{tensor.product_fraction:.1%} of the largest moment"
            )
        if not self.missing:
            return head + "."
        listing = "\n".join(f"  no tensor: {item}" for item in self.missing)
        return (
            head
            + ".\nThis is a lower bound, not the assembly's inertia — "
            + f"{len(self.missing)} occurrence(s) were left out:\n"
            + listing
        )


def _at_or_under(path: str, prefix: str) -> bool:
    """Is `path` the occurrence `prefix`, or one beneath it?

    Compared on whole segments (`bench/foot` does not contain `bench/footplate`), the
    same rule `app.dynamics.assembly._under` applies.
    """
    return path == prefix or path.startswith(prefix + "/")


def roll_up_inertia(
    structure: ProductStructure, measure: ComponentMeasurer
) -> InertiaRollup:
    """Measure every leaf occurrence's inertia, measuring each component once.

    The same contract `mass.roll_up` keeps: each distinct component is measured once
    however many times it is used, a measurer that raises produces a named omission
    rather than a lost machine, and nothing that could not be measured disappears.

    The measurer must answer at `Detail.INERTIA`. `mass.from_document` asks for
    `Detail.FULL`, which does **not** include the tensor — `Detail.INERTIA` is a higher
    level and is never computed speculatively — so a payload from that measurer is a
    named omission here rather than a silent zero. `from_document` below is the one to
    use with this function.
    """
    weighed: list[PlacedInertia] = []
    missing: list[MissingMass] = []
    payloads: dict[str, Mapping[str, Any] | None] = {}
    reasons: dict[str, str] = {}
    total = 0

    for occurrence in structure.occurrences(leaves_only=True):
        total += 1
        component = occurrence.component
        if component not in payloads:
            try:
                payloads[component] = measure(component)
            except Exception as exc:  # noqa: BLE001 - a measurer's failure is data
                payloads[component] = None
                reasons[component] = (
                    f"measuring {component} raised {type(exc).__name__}: {exc}"
                )
        payload = payloads[component]
        if payload is None:
            missing.append(MissingMass(occurrence.path, component, reasons[component]))
            continue
        placed = _place(occurrence, payload)
        if isinstance(placed, MissingMass):
            missing.append(placed)
        else:
            weighed.append(placed)

    return InertiaRollup(
        weighed=tuple(weighed),
        missing=tuple(missing),
        occurrences=total,
        components_measured=len(payloads),
    )


def _place(
    occurrence: Occurrence, payload: Mapping[str, Any]
) -> PlacedInertia | MissingMass:
    """One occurrence's world-axes tensor about its own centre, or why it has none.

    Three conversions happen here and each happens exactly once. The tensor is scaled
    from mm^5 to kg.mm^2 by the component's own density (`mass_kg / volume_mm3`); it is
    rotated from the component's axes into the world by the occurrence's frame; and the
    centre of mass is placed by the same frame. The tensor is **not** translated here —
    it stays about its own centre, and `InertiaRollup.about` does the shifting.
    """
    mass = payload.get(MASS_KG)
    if mass is None or isinstance(mass, bool) or not isinstance(mass, (int, float)):
        return MissingMass(
            occurrence.path,
            occurrence.component,
            f"{occurrence.component} reports no mass, so its inertia cannot be scaled "
            "out of the kernel's second moment of volume. Set the component's material.",
        )

    volume = payload.get(VOLUME_MM3)
    if not isinstance(volume, (int, float)) or isinstance(volume, bool) or volume <= 0.0:
        return MissingMass(
            occurrence.path,
            occurrence.component,
            f"{occurrence.component} reports a mass of {float(mass):g} kg and no usable "
            "volume_mm3, so its density is unknown and the tensor cannot be converted "
            "from mm^5 to kg.mm^2.",
        )

    raw = payload.get(INERTIA_TENSOR_MM5)
    if not _is_three_by_three(raw):
        return MissingMass(
            occurrence.path,
            occurrence.component,
            f"{occurrence.component} carries no inertia_tensor_mm5. Measure it at "
            "Detail.INERTIA — Detail.FULL stops at mass and centre of mass, because the "
            "tensor is a fourth integration nothing computes speculatively.",
        )

    centre = payload.get(CENTRE_OF_MASS_MM)
    if not isinstance(centre, (list, tuple)) or len(centre) != 3:
        return MissingMass(
            occurrence.path,
            occurrence.component,
            f"{occurrence.component} has an inertia tensor but no centre of mass, so "
            "there is no point for the tensor to be about and it cannot be placed.",
        )

    density_kg_mm3 = float(mass) / float(volume)
    local = InertiaTensor.from_rows(raw).scaled(density_kg_mm3)
    placed = local.rotated(occurrence.frame.rotation)
    local_centre: Vec3 = (float(centre[0]), float(centre[1]), float(centre[2]))
    return PlacedInertia(
        path=occurrence.path,
        component=occurrence.component,
        mass_kg=float(mass),
        centre_of_mass_mm=occurrence.frame.point(local_centre),
        tensor_kg_mm2=placed,
    )


def _is_three_by_three(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return False
    return all(
        isinstance(row, (list, tuple))
        and len(row) == 3
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in row)
        for row in value
    )


class InertiaError(ValueError):
    """Raised where a tensor cannot honestly become what the caller asked for."""


def body_diagonal(tensor: InertiaTensor, *, body: str) -> Vec3:
    """`Body.inertia_kg_mm2` for a body whose tensor is `tensor` — or a refusal.

    `app.dynamics.types.Body` takes the **diagonal** of the tensor about the centre of
    mass, three numbers, not six. For a part whose body axes happen to be its principal
    axes that is the whole tensor and nothing is lost. For every other part the products
    of inertia are what turn a rotation about one axis into a moment about the other two
    — which is precisely the bearing load the moment is being computed for — so handing
    over the diagonal alone under-reports a real moment at a plausible magnitude.

    That is the failure this codebase is most careful about, so it is refused rather than
    rounded: an ordinary L-shaped link measures 18% coupling, and 18% of a bearing moment
    is not a rounding. `MAX_PRODUCT_FRACTION` is the line and the refusal names the two
    honest ways past it.
    """
    if tensor.is_near_diagonal:
        return tensor.diagonal
    raise InertiaError(
        f"Body {body!r} has a largest product of inertia "
        f"{tensor.largest_product:.4g} kg.mm2, which is "
        f"{tensor.product_fraction:.1%} of its largest moment "
        f"{tensor.largest_moment:.4g} kg.mm2. Body.inertia_kg_mm2 carries only the "
        "diagonal, and dropping a coupling that size would under-report the moment this "
        "body puts into its joint — in the direction a check passes. Either orient the "
        "body so its axes are its principal axes (then the products vanish and the "
        "diagonal is exact), or treat it as a point mass deliberately, which at least "
        "says so in the result's notes."
    )


def from_document(documents: Mapping[str, Any]) -> ComponentMeasurer:
    """A `ComponentMeasurer` reading each component at `Detail.INERTIA`.

    The sibling of `mass.from_document`, and it exists *because* it asks for a different
    detail level: that one asks for `Detail.FULL`, which stops before the tensor. A
    caller weighing an assembly and then measuring its inertia measures each component
    twice, which is the cost of the tensor being a fourth integration nobody wants
    speculatively — and both roll-ups still measure a component used forty times once.
    """

    def measurer(component: str) -> Mapping[str, Any]:
        from app.kernel.measurement import Detail

        try:
            document = documents[component]
        except KeyError:
            raise KeyError(
                f"no document was supplied for component {component!r}, so its inertia "
                "cannot be measured. Build it, or leave it out of the structure."
            ) from None
        return document.measure(detail=Detail.INERTIA)

    return measurer


__all__ = [
    "CENTRE_OF_MASS_MM",
    "INERTIA_TENSOR_MM5",
    "MASS_KG",
    "MAX_PRODUCT_FRACTION",
    "VOLUME_MM3",
    "InertiaError",
    "InertiaRollup",
    "InertiaTensor",
    "PlacedInertia",
    "body_diagonal",
    "from_document",
    "roll_up_inertia",
]
