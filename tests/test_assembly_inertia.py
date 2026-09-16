"""How the mass is distributed, and every way that answer can be plausibly wrong.

`app.assembly.inertia` exists so a joint *moment* has something to be computed from.
Every defect it can have produces a number of roughly the right size rather than an
error, so the tests are built around the three that would survive a magnitude check:

* **the wrong sign on a product of inertia.** OCCT's off-diagonals are `-∫xy dV`, not
  `+∫xy dV`, and the parallel-axis term follows the same convention. Flip either and the
  products come out at exactly the right magnitude pointing the wrong way.
  `TestTwoPiecesAssembleIntoTheShapeTheyFuseInto` is the oracle: two boxes shifted onto a
  common centre must reproduce the tensor of the L they form, which is computed here from
  arithmetic rather than from the module under test.
* **transposing the rotation.** `R I R^T` and `R^T I R` agree for a 90 degree turn and
  disagree for 30, so `TestARotatedOccurrenceCarriesItsTensorRound` uses 30.
* **dropping the products of inertia.** `Body.inertia_kg_mm2` is a diagonal, and an
  L-shaped link couples 18% of its largest moment into the other two axes. Handing over
  the diagonal alone under-reports a bearing moment, so it is refused —
  `TestADiagonalIsRefusedWhenTheCouplingIsReal` pins the refusal *and* that a genuinely
  diagonal tensor passes, because a guard that refuses everything is not a guard.

The payloads here are written by hand from closed-form integrals rather than measured, so
the suite stays offline and under a second. The same arithmetic was checked against the
real OCCT kernel on 2026-09-16 before any of it was asserted — a box's centroidal tensor,
the L's product of inertia, the two-piece assembly and the 30 degree rotation all agree to
1e-15 of the tensor's magnitude. **These tests were written on Linux and have not been
run**; the Windows machine runs them.
"""

from __future__ import annotations

import math

import pytest

from app.assembly.inertia import (
    MAX_PRODUCT_FRACTION,
    InertiaError,
    InertiaTensor,
    body_diagonal,
    roll_up_inertia,
)
from app.assembly.structure import Component, Instance, ProductStructure
from app.dynamics.pose import Frame

# -- closed-form fixtures -----------------------------------------------------

#: Steel, and the density is only ever used as `mass / volume`, so any consistent pair
#: would do. Named rather than inlined because three fixtures must agree on it.
RHO_KG_M3 = 7850.0
RHO_KG_MM3 = RHO_KG_M3 * 1e-9


def _box_payload(a: float, b: float, c: float, origin=(0.0, 0.0, 0.0)) -> dict:
    """A solid box `a x b x c` with one corner at `origin`, as the kernel would report it.

    The second moments of *volume* about the centroid are `V(b^2+c^2)/12` and its cyclic
    partners, with no products of inertia — a box's own axes are its principal axes. This
    is the arithmetic the real kernel was checked against, not a recording of it.
    """
    volume = a * b * c
    return {
        "mass_kg": volume * RHO_KG_MM3,
        "volume_mm3": volume,
        "centre_of_mass_mm": [origin[0] + a / 2, origin[1] + b / 2, origin[2] + c / 2],
        "inertia_tensor_mm5": [
            [volume * (b * b + c * c) / 12.0, 0.0, 0.0],
            [0.0, volume * (a * a + c * c) / 12.0, 0.0],
            [0.0, 0.0, volume * (a * a + b * b) / 12.0],
        ],
    }


def _structure(*placed: tuple[str, str, Frame], root: str = "rig") -> ProductStructure:
    """A flat assembly: one root holding `(component, tag, placement)` instances."""
    return ProductStructure(
        root=root,
        components=[
            Component(
                name=root,
                instances=tuple(
                    Instance(component, tag, placement=frame)
                    for component, tag, frame in placed
                ),
            ),
            *[Component(name=component) for component, _, _ in placed],
        ],
    )


def _measurer(payloads: dict, calls: list[str] | None = None):
    def measure(component: str):
        if calls is not None:
            calls.append(component)
        return payloads[component]

    return measure


def _identity() -> tuple[float, ...]:
    return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def _about_z(degrees: float) -> tuple[float, ...]:
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return (c, -s, 0.0, s, c, 0.0, 0.0, 0.0, 1.0)


# -- the units boundary -------------------------------------------------------


class TestTheKernelsVolumeMomentBecomesAMassMoment:
    """mm^5 in, kg.mm^2 out, converted once by the component's own density."""

    def test_a_single_box_reports_its_closed_form_inertia(self):
        payload = _box_payload(200.0, 40.0, 20.0)
        structure = _structure(("link", "link", Frame()))
        rollup = roll_up_inertia(structure, _measurer({"link": payload}))

        assert rollup.complete
        tensor = rollup.about_centre()
        mass = 200.0 * 40.0 * 20.0 * RHO_KG_MM3
        assert tensor.xx == pytest.approx(mass * (40.0**2 + 20.0**2) / 12.0, rel=1e-12)
        assert tensor.yy == pytest.approx(mass * (200.0**2 + 20.0**2) / 12.0, rel=1e-12)
        assert tensor.zz == pytest.approx(mass * (200.0**2 + 40.0**2) / 12.0, rel=1e-12)

    def test_the_density_comes_from_the_payload_not_from_a_constant(self):
        """Two components of different materials each convert with their own density.

        The alternative — one `1e-9` constant for kg/m3 to kg/mm3 — would be a unit
        nothing checks, and would be silently wrong for a mixed-material assembly. Here
        the heavy box is ten times the density of the light one and its tensor is ten
        times as large, with identical geometry.
        """
        light = _box_payload(10.0, 10.0, 10.0)
        heavy = dict(light, mass_kg=light["mass_kg"] * 10.0)
        structure = _structure(("light", "a", Frame()), ("heavy", "b", Frame()))
        rollup = roll_up_inertia(
            structure, _measurer({"light": light, "heavy": heavy})
        )

        by_path = {item.component: item.tensor_kg_mm2 for item in rollup.weighed}
        assert by_path["heavy"].xx == pytest.approx(by_path["light"].xx * 10.0, rel=1e-12)

    def test_a_tensor_of_nothing_has_no_product_fraction_rather_than_infinity(self):
        assert InertiaTensor().product_fraction == 0.0
        assert InertiaTensor().is_near_diagonal


# -- the oracle ---------------------------------------------------------------


class TestTwoPiecesAssembleIntoTheShapeTheyFuseInto:
    """The parallel-axis theorem, checked against the whole it builds.

    Region A is 100 x 50 x 10 at the origin; region B is 50 x 50 x 10 sitting on top of
    it in y. Together they are an L whose tensor about the common centre of mass is
    computed here from the integrals, so nothing in this test is the module's own answer
    read back.
    """

    T = 10.0

    def _rollup(self):
        a = _box_payload(100.0, 50.0, self.T)
        b = _box_payload(50.0, 50.0, self.T)
        structure = _structure(
            ("a", "base", Frame(origin_mm=(0.0, 0.0, 0.0))),
            ("b", "wing", Frame(origin_mm=(0.0, 50.0, 0.0))),
        )
        return roll_up_inertia(structure, _measurer({"a": a, "b": b}))

    def test_the_centre_of_mass_is_the_mass_weighted_mean(self):
        rollup = self._rollup()
        centre = rollup.centre_of_mass_mm
        # 5000 mm2 at x=50 and 2500 mm2 at x=25, so 125/3 in both x and y.
        assert centre[0] == pytest.approx(125.0 / 3.0, rel=1e-12)
        assert centre[1] == pytest.approx(125.0 / 3.0, rel=1e-12)
        assert centre[2] == pytest.approx(self.T / 2, rel=1e-12)

    def test_the_product_of_inertia_is_negative_of_the_integral(self):
        """`xy` is `-∫xy dm`, which for this L is positive.

        The analytic `∫(x-xc)(y-yc) dV` over the two rectangles is `-2.0833e7 mm^5`; the
        real kernel reports `tensor[0][1] = +2.0833e7` for the fused shape, and so must
        this. Taking the other convention gives the same magnitude with the wrong sign,
        which is why the sign is asserted and not just the size.
        """
        xc = yc = 125.0 / 3.0

        def rect(x0, x1, y0, y1):
            ix = (x1**2 - x0**2) / 2 - xc * (x1 - x0)
            iy = (y1**2 - y0**2) / 2 - yc * (y1 - y0)
            return ix * iy * self.T

        integral = rect(0, 100, 0, 50) + rect(0, 50, 50, 100)
        assert integral < 0.0  # the L leans away from the diagonal

        tensor = self._rollup().about_centre()
        assert tensor.xy == pytest.approx(-integral * RHO_KG_MM3, rel=1e-12)
        assert tensor.xy > 0.0

    def test_the_diagonal_matches_the_integral_over_both_rectangles(self):
        rollup = self._rollup()
        tensor = rollup.about_centre()
        xc = yc = 125.0 / 3.0
        zc = self.T / 2

        def moment(x0, x1, y0, y1, axis):
            """Second moment of volume of one rectangle about the common centre."""
            # ∫(u-uc)^2 du over [u0, u1]
            def sq(u0, u1, uc):
                return ((u1 - uc) ** 3 - (u0 - uc) ** 3) / 3.0

            def lin(u0, u1):
                return u1 - u0

            ix, iy, iz = sq(x0, x1, xc), sq(y0, y1, yc), sq(0, self.T, zc)
            lx, ly, lz = lin(x0, x1), lin(y0, y1), self.T
            if axis == "x":
                return iy * lx * lz + iz * lx * ly
            if axis == "y":
                return ix * ly * lz + iz * lx * ly
            return ix * ly * lz + iy * lx * lz

        for axis, got in (("x", tensor.xx), ("y", tensor.yy), ("z", tensor.zz)):
            expected = (
                moment(0, 100, 0, 50, axis) + moment(0, 50, 50, 100, axis)
            ) * RHO_KG_MM3
            assert got == pytest.approx(expected, rel=1e-12)

    def test_shifting_to_a_point_that_is_not_the_centre_adds_m_d_squared(self):
        rollup = self._rollup()
        centre = rollup.centre_of_mass_mm
        at_centre = rollup.about(centre)
        offset = (centre[0] + 30.0, centre[1], centre[2])
        shifted = rollup.about(offset)
        assert shifted.yy == pytest.approx(
            at_centre.yy + rollup.mass_kg * 30.0**2, rel=1e-12
        )
        assert shifted.xx == pytest.approx(at_centre.xx, rel=1e-12)


# -- rotation -----------------------------------------------------------------


class TestARotatedOccurrenceCarriesItsTensorRound:
    def test_thirty_degrees_tells_the_transpose_apart(self):
        """`R I R^T`, and 30 degrees is chosen because 90 would not discriminate.

        For a tensor with a single product of inertia a quarter turn negates `xy` under
        both conventions. At 30 degrees they differ, and the arithmetic below is the
        standard similarity transform written out rather than the module's own.
        """
        tensor = InertiaTensor(xx=2.0, yy=5.0, zz=9.0, xy=1.5)
        rotation = _about_z(30.0)
        c, s = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))

        turned = tensor.rotated(rotation)

        # R I R^T for a rotation about z, written out.
        assert turned.xx == pytest.approx(
            c * c * tensor.xx - 2 * c * s * tensor.xy + s * s * tensor.yy, rel=1e-12
        )
        assert turned.yy == pytest.approx(
            s * s * tensor.xx + 2 * c * s * tensor.xy + c * c * tensor.yy, rel=1e-12
        )
        assert turned.xy == pytest.approx(
            c * s * (tensor.xx - tensor.yy) + (c * c - s * s) * tensor.xy, rel=1e-12
        )
        assert turned.zz == pytest.approx(tensor.zz, rel=1e-12)

    def test_a_quarter_turn_negates_the_product_of_inertia(self):
        tensor = InertiaTensor(xx=2.0, yy=5.0, zz=9.0, xy=1.5)
        turned = tensor.rotated(_about_z(90.0))
        assert turned.xy == pytest.approx(-1.5, rel=1e-12)
        assert turned.xx == pytest.approx(5.0, rel=1e-12)
        assert turned.yy == pytest.approx(2.0, rel=1e-12)

    def test_the_identity_leaves_a_tensor_alone(self):
        tensor = InertiaTensor(xx=2.0, yy=5.0, zz=9.0, xy=1.5, xz=0.5, yz=-0.25)
        assert tensor.rotated(_identity()) == tensor

    def test_rotating_the_whole_assembly_rotates_its_tensor(self):
        """A rig turned bodily about z reports the tensor of the turned rig.

        The occurrence frames do the turning, so this exercises `rotated` through the
        roll-up rather than directly — the path a real structure takes.
        """
        payloads = {"a": _box_payload(100.0, 50.0, 10.0), "b": _box_payload(50.0, 50.0, 10.0)}
        upright = roll_up_inertia(
            _structure(
                ("a", "base", Frame(origin_mm=(0.0, 0.0, 0.0))),
                ("b", "wing", Frame(origin_mm=(0.0, 50.0, 0.0))),
            ),
            _measurer(payloads),
        ).about_centre()

        quarter = _about_z(90.0)
        turned = roll_up_inertia(
            _structure(
                ("a", "base", Frame(quarter, (0.0, 0.0, 0.0))),
                ("b", "wing", Frame(quarter, (-50.0, 0.0, 0.0))),
            ),
            _measurer(payloads),
        ).about_centre()

        assert turned.xx == pytest.approx(upright.yy, rel=1e-12)
        assert turned.yy == pytest.approx(upright.xx, rel=1e-12)
        assert turned.zz == pytest.approx(upright.zz, rel=1e-12)
        assert turned.xy == pytest.approx(-upright.xy, rel=1e-12)


# -- the diagonal refusal -----------------------------------------------------


class TestADiagonalIsRefusedWhenTheCouplingIsReal:
    def test_a_box_passes_because_its_axes_are_its_principal_axes(self):
        tensor = InertiaTensor(xx=2.0, yy=5.0, zz=9.0)
        assert body_diagonal(tensor, body="link") == (2.0, 5.0, 9.0)

    def test_an_l_shaped_link_is_refused_with_the_ratio_named(self):
        # The measured L: 899.5 largest moment, 163.5 largest product, 18.2%.
        tensor = InertiaTensor(xx=454.6, yy=454.6, zz=899.5, xy=163.5)
        assert tensor.product_fraction == pytest.approx(163.5 / 899.5, rel=1e-9)
        with pytest.raises(InertiaError) as caught:
            body_diagonal(tensor, body="arm/ell.1")
        message = str(caught.value)
        assert "arm/ell.1" in message
        assert "18.2%" in message
        assert "principal axes" in message

    def test_the_threshold_is_the_line_and_both_sides_of_it_are_tested(self):
        """A guard that refuses everything is not a guard, so both sides are pinned."""
        moment = 100.0
        just_under = InertiaTensor(zz=moment, xy=moment * MAX_PRODUCT_FRACTION * 0.99)
        just_over = InertiaTensor(zz=moment, xy=moment * MAX_PRODUCT_FRACTION * 1.01)
        assert body_diagonal(just_under, body="fine") == (0.0, 0.0, moment)
        with pytest.raises(InertiaError):
            body_diagonal(just_over, body="coupled")


# -- omissions ----------------------------------------------------------------


class TestAnOmissionIsNamedAndNeverZero:
    """A tensor that could not be measured must not become a tensor of zero.

    Zero inertia is not "unknown inertia": it is a body that offers no resistance to
    angular acceleration, which makes every moment above it *smaller*. That is the
    direction a check passes, so each of the four ways a payload can fail to yield a
    tensor produces a named `MissingMass` and an incomplete roll-up.
    """

    def _one(self, payload):
        return roll_up_inertia(
            _structure(("part", "part", Frame())), _measurer({"part": payload})
        )

    def test_a_payload_measured_at_full_detail_carries_no_tensor(self):
        """The trap this module was written around: `Detail.FULL` stops before inertia.

        `mass.from_document` asks for `Detail.FULL`, so its payloads have a mass and a
        centre and no `inertia_tensor_mm5`. Reading one here must say so rather than
        assemble a machine out of nothing.
        """
        rollup = self._one({"mass_kg": 1.0, "volume_mm3": 1000.0, "centre_of_mass_mm": [0, 0, 0]})
        assert not rollup.complete
        assert rollup.missing[0].reason.count("Detail.INERTIA") == 1
        assert "Detail.FULL" in rollup.missing[0].reason

    def test_no_mass_means_no_density_means_no_tensor(self):
        rollup = self._one(
            {"volume_mm3": 1000.0, "centre_of_mass_mm": [0, 0, 0], "inertia_tensor_mm5": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        )
        assert not rollup.complete
        assert "no mass" in rollup.missing[0].reason

    def test_no_volume_means_the_density_cannot_be_recovered(self):
        rollup = self._one(
            {"mass_kg": 1.0, "centre_of_mass_mm": [0, 0, 0], "inertia_tensor_mm5": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        )
        assert not rollup.complete
        assert "volume_mm3" in rollup.missing[0].reason

    def test_a_zero_volume_is_refused_rather_than_dividing(self):
        rollup = self._one(
            {"mass_kg": 1.0, "volume_mm3": 0.0, "centre_of_mass_mm": [0, 0, 0], "inertia_tensor_mm5": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        )
        assert not rollup.complete

    def test_a_tensor_with_no_centre_has_no_point_to_be_about(self):
        rollup = self._one(
            {"mass_kg": 1.0, "volume_mm3": 1000.0, "inertia_tensor_mm5": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        )
        assert not rollup.complete
        assert "centre of mass" in rollup.missing[0].reason

    def test_a_measurer_that_raises_names_the_component_and_does_not_escape(self):
        def explode(component: str):
            raise RuntimeError("the document was never built")

        rollup = roll_up_inertia(_structure(("part", "part", Frame())), explode)
        assert not rollup.complete
        assert "RuntimeError" in rollup.missing[0].reason
        assert "the document was never built" in rollup.missing[0].reason

    def test_one_bad_component_does_not_cost_the_others(self):
        good = _box_payload(10.0, 10.0, 10.0)
        bad = {"mass_kg": 1.0, "volume_mm3": 1000.0, "centre_of_mass_mm": [0, 0, 0]}
        rollup = roll_up_inertia(
            _structure(("good", "g", Frame()), ("bad", "b", Frame())),
            _measurer({"good": good, "bad": bad}),
        )
        assert not rollup.complete
        assert len(rollup.weighed) == 1
        assert len(rollup.missing) == 1
        assert rollup.weighed[0].component == "good"


# -- the graph paying for itself ----------------------------------------------


class TestEachComponentIsMeasuredOnceHoweverOftenItIsUsed:
    def test_forty_bolts_are_one_integration(self):
        calls: list[str] = []
        payload = _box_payload(5.0, 5.0, 5.0)
        structure = ProductStructure(
            root="rig",
            components=[
                Component(
                    name="rig",
                    instances=tuple(
                        Instance("bolt", "bolt", index=n + 1, placement=Frame(origin_mm=(10.0 * n, 0.0, 0.0)))
                        for n in range(40)
                    ),
                ),
                Component(name="bolt"),
            ],
        )
        rollup = roll_up_inertia(structure, _measurer({"bolt": payload}, calls))

        assert calls == ["bolt"]
        assert rollup.occurrences == 40
        assert rollup.components_measured == 1
        assert len(rollup.weighed) == 40

    def test_forty_bolts_in_a_row_have_the_inertia_of_a_row_of_bolts(self):
        """The cache must not also collapse the *placements*.

        A cache that returned one occurrence for forty would give a fortieth of the
        inertia and a plausible-looking tensor, so the sum is checked against the
        parallel-axis series rather than against the count alone.
        """
        payload = _box_payload(5.0, 5.0, 5.0)
        spacing = 10.0
        n = 40
        structure = ProductStructure(
            root="rig",
            components=[
                Component(
                    name="rig",
                    instances=tuple(
                        Instance("bolt", "bolt", index=k + 1, placement=Frame(origin_mm=(spacing * k, 0.0, 0.0)))
                        for k in range(n)
                    ),
                ),
                Component(name="bolt"),
            ],
        )
        rollup = roll_up_inertia(structure, _measurer({"bolt": payload}))
        one = payload["mass_kg"]
        centre = rollup.centre_of_mass_mm

        assert rollup.mass_kg == pytest.approx(one * n, rel=1e-12)
        # Centres are at 2.5 + 10k, so the mean is 2.5 + 10*(n-1)/2.
        assert centre[0] == pytest.approx(2.5 + spacing * (n - 1) / 2, rel=1e-12)

        own = payload["inertia_tensor_mm5"][2][2] * RHO_KG_MM3
        offsets = [2.5 + spacing * k - centre[0] for k in range(n)]
        expected = sum(own + one * d * d for d in offsets)
        assert rollup.about(centre).zz == pytest.approx(expected, rel=1e-12)


# -- sub-trees ----------------------------------------------------------------


class TestABodyTakesOnlyWhatIsUnderIt:
    def _bench(self) -> ProductStructure:
        """A bench with a `foot` and a `footplate`, which a prefix match would confuse."""
        return ProductStructure(
            root="bench",
            components=[
                Component(
                    name="bench",
                    instances=(
                        Instance("foot", "foot", placement=Frame(origin_mm=(0.0, 0.0, 0.0))),
                        Instance("plate", "footplate", placement=Frame(origin_mm=(100.0, 0.0, 0.0))),
                    ),
                ),
                Component(name="foot"),
                Component(name="plate"),
            ],
        )

    def test_under_compares_whole_segments_not_string_prefixes(self):
        payloads = {"foot": _box_payload(10.0, 10.0, 10.0), "plate": _box_payload(20.0, 20.0, 5.0)}
        rollup = roll_up_inertia(self._bench(), _measurer(payloads))

        only_foot = rollup.under("bench/foot.1")
        assert [i.path for i in only_foot.weighed] == ["bench/foot.1"]
        assert only_foot.occurrences == 1
        assert only_foot.components_measured == 1

    def test_a_sub_rollup_recounts_rather_than_inheriting_the_machines_totals(self):
        payloads = {"foot": _box_payload(10.0, 10.0, 10.0), "plate": _box_payload(20.0, 20.0, 5.0)}
        rollup = roll_up_inertia(self._bench(), _measurer(payloads))
        assert rollup.occurrences == 2
        assert rollup.under("bench/footplate.1").occurrences == 1

    def test_the_whole_root_is_everything(self):
        payloads = {"foot": _box_payload(10.0, 10.0, 10.0), "plate": _box_payload(20.0, 20.0, 5.0)}
        rollup = roll_up_inertia(self._bench(), _measurer(payloads))
        assert rollup.under("bench").occurrences == 2


# -- the reading a report gets ------------------------------------------------


class TestTheSummaryTellsAReaderWhatIsMissing:
    def test_a_complete_rollup_reports_its_diagonal_and_its_coupling(self):
        rollup = roll_up_inertia(
            _structure(("link", "link", Frame())),
            _measurer({"link": _box_payload(200.0, 40.0, 20.0)}),
        )
        text = rollup.summary()
        assert "1 of 1 occurrences carry an inertia tensor" in text
        assert "kg.mm2" in text
        assert "largest product" in text
        assert "lower bound" not in text

    def test_an_incomplete_rollup_says_it_is_a_lower_bound(self):
        rollup = roll_up_inertia(
            _structure(("good", "g", Frame()), ("bad", "b", Frame())),
            _measurer(
                {
                    "good": _box_payload(10.0, 10.0, 10.0),
                    "bad": {"mass_kg": 1.0, "volume_mm3": 10.0, "centre_of_mass_mm": [0, 0, 0]},
                }
            ),
        )
        text = rollup.summary()
        assert "lower bound" in text
        assert "no tensor: rig/b.1" in text
        assert not rollup

    def test_nothing_at_all_says_so_rather_than_reporting_zero(self):
        from app.assembly.inertia import InertiaRollup

        empty = InertiaRollup()
        assert empty.summary() == "Nothing to measure."
        assert empty.about_centre() is None
        assert empty.centre_of_mass_mm is None
        assert not empty
