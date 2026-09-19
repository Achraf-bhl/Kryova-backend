"""Reading CATIA assembly constraints as joint declarations — E9 task 2's hand-written input.

`app/dynamics/assembly.py` derives a `Mechanism` from the product graph, and the one thing it
cannot derive is the **joints**: which occurrence turns about which other, and on what axis.
That has been supplied by hand since E9.2. An engineer who has assembled a machine in CATIA has
already said all of it, in the assembly constraints — this module is the translation.

**Measured on a V5-R33 seat, 2026-09-19** (THE QUEUE E6), and the first sitting got it wrong in
a way worth keeping:

* A constraint's operands are **not** `ConstraintElement1` / `ConstraintElement2`. Those names
  do not exist, late binding says `AttributeError`, and it reads exactly like "the API does not
  expose its operands". The real member is the method **`GetConstraintElement(n)`**, declared in
  `CATIA V5 MecModInterfaces` (`{0D90A5C9-3B08-11D1-A26C-0000F87546FD}`). Read the type library
  before concluding an API is absent — the same lesson DMU Kinematics taught through a licence.
* `element.DisplayName` is the whole prize: `'E6Rig/E6Base.1/!E6Base/Plan xy'` carries the
  **occurrence path** *and* the geometry named on it, which is exactly `JointDeclaration.child`
  and what the axis must be resolved from.
* **`GetConstraintVisuLocation` is not the axis.** It returns without error and answers
  `((0,0,0), (0,0,0))` on a plane–plane coincidence: it is where CATIA draws the constraint
  glyph, and an unset one at that. A zero vector is not a direction, so `axis` is **not**
  recoverable from the constraint and has to come from the geometry the DisplayName names.
  Reporting the glyph as a joint axis would be a plausible wrong number, which is why
  `read_constraints` refuses to invent one.
* Several properties are readable **only on the constraint types they apply to**. `Side`,
  `DistanceConfig`, `DistanceDirection` and `AngleSector` raise
  *"Défaillance irrémédiable"* on a Coincidence — an alarming message for "not applicable".
  Never read them unconditionally.
* A `Fix` is mono-element: `GetConstraintElement(2)` is refused, not empty.

**What this module does not do.** It does not talk to CATIA. The daemon reads the constraints
and sends the records here as data, so the translation is testable with no seat — the same split
`app/design/` uses for `execute`. Nothing here imports `win32com`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from app.dynamics.assembly import JointDeclaration
from app.dynamics.types import JointKind

#: CATIA's assembly constraint type codes, read back from the names CATIA assigned on a
#: French seat (`Coïncidence.1`, `Décalage.2`, `Fixe.3`) on 2026-09-02 and re-measured
#: 2026-09-19. The codes are not localised; the names are, which is why the codes are what
#: this module keys on.
CONSTRAINT_TYPES: Final[dict[int, str]] = {
    0: "fix",
    1: "offset",
    2: "coincidence",
    4: "tangency",
    6: "angle",
    8: "parallelism",
    11: "perpendicularity",
}

#: Which constraint types carry a `Dimension`, so a reader never asks the others for one.
#: Measured: `Offset` answered 25.0, `Fix` answered 0.0, `Coincidence` had none at all.
DIMENSIONED: Final[frozenset[int]] = frozenset({1, 6})

#: `E6Rig/E6Base.1/!E6Base/Plan xy` -> occurrence `E6Base.1`, geometry `Plan xy`.
#: The leading segment is the root product and the `!` introduces the in-part path, whose
#: own first segment repeats the part number. Written as one expression because every part
#: of it was read off a real `DisplayName` rather than inferred from documentation.
_DISPLAY_NAME: Final = re.compile(
    r"^(?P<root>[^/]+)/(?P<occurrence>[^/]+)/!(?P<part>[^/]+)/(?P<geometry>.+)$"
)


@dataclass(frozen=True)
class ConstraintElement:
    """One operand of a constraint, as `GetConstraintElement(n).DisplayName` gives it."""

    display_name: str

    @property
    def parsed(self) -> re.Match[str] | None:
        return _DISPLAY_NAME.match(self.display_name)

    @property
    def occurrence(self) -> str | None:
        """The occurrence path, which is `JointDeclaration.child`'s vocabulary."""
        match = self.parsed
        return match.group("occurrence") if match else None

    @property
    def geometry(self) -> str | None:
        """What the constraint names on that occurrence — `Plan xy`, a face, an axis.

        **Localised.** On a French seat the origin planes are `Plan xy`, `Plan yz`,
        `Plan zx`; `CreateReferenceFromName` refuses `PlaneXY` outright. Anything reading
        this for meaning must go through a language table, never an English literal.
        """
        match = self.parsed
        return match.group("geometry") if match else None


@dataclass(frozen=True)
class ConstraintRecord:
    """One assembly constraint, as the daemon can read it without interpreting it.

    Deliberately close to the COM shape: this is a transcript, and the judgement is
    `read_constraints`'. A record that already decided what kind of joint it was would put
    the decision on the workstation, where it cannot be tested.
    """

    name: str
    type_code: int
    elements: tuple[ConstraintElement, ...]
    dimension_mm: float | None = None
    inactive: bool = False

    @property
    def kind(self) -> str:
        return CONSTRAINT_TYPES.get(self.type_code, "unknown")

    @property
    def occurrences(self) -> tuple[str, ...]:
        found = [element.occurrence for element in self.elements]
        return tuple(name for name in found if name is not None)


@dataclass(frozen=True)
class ConstraintReading:
    """What the constraints said, and everything they could not say.

    `unresolved` is not decoration. A constraint set that names an occurrence this cannot
    parse, or that implies a joint whose axis nothing supplies, is a mechanism nobody should
    drive — and the honest shape is to say which, not to drop it.
    """

    joints: tuple[JointDeclaration, ...] = ()
    grounded: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def read_constraints(
    records: Iterable[ConstraintRecord],
    *,
    axes: Sequence[tuple[str, tuple[float, float, float]]] | None = None,
) -> ConstraintReading:
    """Turn a product's constraints into joint declarations, or say why not.

    `axes` supplies what the constraints cannot: the joint axis in the child part's own
    coordinates, keyed by occurrence. It is **required for any moving joint** and there is
    no default — `GetConstraintVisuLocation` answers a zero vector, and inventing an axis
    is exactly the plausible wrong number this codebase refuses everywhere else. A joint
    with no axis supplied is reported `unresolved` rather than declared along +Z.

    Two constraints between the same pair make one joint, because that is what they are: a
    plane coincidence plus an offset is one relationship, not two.
    """
    by_axis = dict(axes or ())
    joints: list[JointDeclaration] = []
    grounded: list[str] = []
    unresolved: list[str] = []
    notes: list[str] = []
    seen: set[tuple[str, str]] = set()

    for record in records:
        if record.inactive:
            notes.append(
                f"{record.name} is deactivated in CATIA, so it says nothing about the "
                "machine and is not read as a joint."
            )
            continue

        if record.type_code not in CONSTRAINT_TYPES:
            unresolved.append(
                f"{record.name}: constraint type {record.type_code} is not one this reads. "
                f"Known: {', '.join(sorted(CONSTRAINT_TYPES.values()))}."
            )
            continue

        occurrences = record.occurrences
        if len(occurrences) != len(record.elements):
            unresolved.append(
                f"{record.name}: an operand's DisplayName did not parse "
                f"({', '.join(element.display_name for element in record.elements)}). "
                "A joint cannot be declared against an occurrence nobody can name."
            )
            continue

        if record.kind == "fix":
            grounded.extend(occurrences)
            continue

        if len(occurrences) < 2:
            unresolved.append(
                f"{record.name}: a {record.kind} names {len(occurrences)} occurrence(s), so "
                "there is no pair to declare a joint between."
            )
            continue

        child, parent = occurrences[0], occurrences[1]
        pair = (child, parent)
        if pair in seen or (parent, child) in seen:
            notes.append(
                f"{record.name} constrains {child} to {parent}, which an earlier constraint "
                "already declared. Two constraints between one pair are one joint."
            )
            continue
        seen.add(pair)

        axis = by_axis.get(child)
        if axis is None:
            unresolved.append(
                f"{record.name}: {child} to {parent} is a joint, and no axis was supplied "
                f"for {child}. CATIA's constraint does not carry one — "
                "GetConstraintVisuLocation answers a zero vector — so it has to come from "
                "the geometry the constraint names "
                f"({record.elements[0].geometry!r}). Supply it in `axes`."
            )
            continue

        joints.append(
            JointDeclaration(
                name=record.name,
                kind=_joint_kind(record.kind),
                child=child,
                parent=parent,
                axis=axis,
            )
        )

    return ConstraintReading(
        joints=tuple(joints),
        grounded=tuple(dict.fromkeys(grounded)),
        unresolved=tuple(unresolved),
        notes=tuple(notes),
    )


def _joint_kind(constraint_kind: str) -> JointKind:
    """The joint a constraint implies **on its own**, which is deliberately conservative.

    A single coincidence of two planes leaves three degrees of freedom, and CATIA's own
    Assembly Constraints Conversion decides a joint from the *set*. This maps one
    constraint, so it answers `revolute` only where the constraint is about an axis and
    `fixed` where the constraint removes everything; anything else is the caller's to
    refine with `NbDof` once a mechanism exists.
    """
    if constraint_kind in {"coincidence", "offset"}:
        return "revolute"
    if constraint_kind == "parallelism":
        return "prismatic"
    return "fixed"


__all__ = [
    "CONSTRAINT_TYPES",
    "DIMENSIONED",
    "ConstraintElement",
    "ConstraintReading",
    "ConstraintRecord",
    "read_constraints",
]
