"""Assembly constraints read as joint declarations — THE QUEUE E6, measured 2026-09-19.

E6 asks for a `DmuKinematicsEngine` and notes in passing that it "also settles whether CATIA's
assembly constraints can be read as joint declarations, which is the one input E9.2 still takes
by hand". That half is settled here, and it needed no `AddJoint` — which matters, because
feeding `AddJoint` a non-empty doubles array killed CNEXT on 2026-09-17.

**The answer is yes for the pair, no for the axis**, and the first sitting got the first half
wrong. Probing `ConstraintElement1` / `ConstraintElement2` gives `AttributeError` under late
binding, which reads as "constraints do not expose their operands". Those property names simply
do not exist: `CATIA V5 MecModInterfaces` declares the **method** `GetConstraintElement(n)`.
Read the type library before concluding an API is absent.

Every constant below came off a real V5-R33 seat, from a two-part product with a plane
coincidence, a 25 mm offset and a fix:

    element 1  DisplayName='E6Rig/E6Base.1/!E6Base/Plan xy'
    element 2  DisplayName='E6Rig/E6Arm.1/!E6Arm/Plan xy'
    Décalage.2 Type=1 Dimension=25.0 Side=2 DistanceConfig=1
    Fixe.3     Type=0 element 2 REFUSED (mono-element, by design)
    GetConstraintVisuLocation -> ((0,0,0), (0,0,0))    <- not an axis

Offline: no seat, no database, no bridge. `app/dynamics/catia_constraints.py` imports no COM.
"""

from __future__ import annotations

import pytest

from app.dynamics.catia_constraints import (
    CONSTRAINT_TYPES,
    ConstraintElement,
    ConstraintRecord,
    read_constraints,
)

#: Exactly the strings the seat produced.
BASE_XY = "E6Rig/E6Base.1/!E6Base/Plan xy"
ARM_XY = "E6Rig/E6Arm.1/!E6Arm/Plan xy"
BASE_YZ = "E6Rig/E6Base.1/!E6Base/Plan yz"
ARM_YZ = "E6Rig/E6Arm.1/!E6Arm/Plan yz"

Z = (0.0, 0.0, 1.0)


def coincidence(name: str = "Coïncidence.1") -> ConstraintRecord:
    return ConstraintRecord(
        name=name,
        type_code=2,
        elements=(ConstraintElement(BASE_XY), ConstraintElement(ARM_XY)),
    )


def offset(name: str = "Décalage.2") -> ConstraintRecord:
    return ConstraintRecord(
        name=name,
        type_code=1,
        elements=(ConstraintElement(BASE_YZ), ConstraintElement(ARM_YZ)),
        dimension_mm=25.0,
    )


def fix(name: str = "Fixe.3") -> ConstraintRecord:
    """Mono-element on purpose: the seat refuses `GetConstraintElement(2)` on a Fix."""
    return ConstraintRecord(name=name, type_code=0, elements=(ConstraintElement(BASE_XY),))


class TestTheDisplayNameIsTheWholePrize:
    def test_the_occurrence_is_recoverable(self) -> None:
        """`JointDeclaration.child` is an occurrence path, and this is where it comes
        from. Without it there is no joint to declare against anything."""
        assert ConstraintElement(BASE_XY).occurrence == "E6Base.1"
        assert ConstraintElement(ARM_XY).occurrence == "E6Arm.1"

    def test_the_geometry_is_recoverable(self) -> None:
        assert ConstraintElement(BASE_XY).geometry == "Plan xy"
        assert ConstraintElement(ARM_YZ).geometry == "Plan yz"

    def test_the_geometry_name_is_localised(self) -> None:
        """**The trap.** `Plan xy`, not `PlaneXY` — and `CreateReferenceFromName` refuses
        the English spelling outright, measured twice, seventeen days apart. Anything
        reading this for meaning goes through a language table."""
        assert ConstraintElement(BASE_XY).geometry == "Plan xy"
        assert "PlaneXY" not in BASE_XY

    def test_a_name_that_does_not_parse_is_None_rather_than_a_guess(self) -> None:
        for spelling in ("", "nonsense", "A/B", "no-slashes-at-all"):
            assert ConstraintElement(spelling).occurrence is None


class TestWhatTheConstraintsDeclare:
    def test_a_pair_becomes_a_joint_when_an_axis_is_supplied(self) -> None:
        """The axis is keyed on the **child**, which is the constraint's first operand —
        `JointDeclaration.at_mm` and `axis` are in the child part's own coordinates, so
        the parent's axis would be in the wrong frame. Written the other way round first,
        and the refusal caught it."""
        reading = read_constraints([coincidence()], axes=[("E6Base.1", Z)])

        assert not reading.unresolved
        assert len(reading.joints) == 1
        joint = reading.joints[0]
        assert (joint.child, joint.parent) == ("E6Base.1", "E6Arm.1")
        assert joint.axis == Z

    def test_an_axis_for_the_parent_alone_does_not_satisfy_it(self) -> None:
        """The guard on the one above: supplying the wrong occurrence's axis must not
        quietly produce a joint whose axis is in another part's frame."""
        reading = read_constraints([coincidence()], axes=[("E6Arm.1", Z)])

        assert reading.joints == ()
        assert "no axis was supplied for E6Base.1" in reading.unresolved[0]

    def test_a_fix_grounds_its_occurrence_and_is_not_a_joint(self) -> None:
        reading = read_constraints([fix()])

        assert reading.grounded == ("E6Base.1",)
        assert reading.joints == ()

    def test_two_constraints_on_one_pair_are_one_joint(self) -> None:
        """A plane coincidence plus an offset is one relationship. Declaring two would
        give the mechanism a degree of freedom nobody built."""
        reading = read_constraints(
            [coincidence(), offset()], axes=[("E6Base.1", Z), ("E6Arm.1", Z)]
        )

        assert len(reading.joints) == 1
        assert any("already declared" in note for note in reading.notes)

    def test_a_deactivated_constraint_says_nothing(self) -> None:
        """`Deactivate()` is how an engineer parks a constraint. Reading it as a joint
        would declare a machine they deliberately disconnected."""
        record = ConstraintRecord(
            name="Coïncidence.1",
            type_code=2,
            elements=coincidence().elements,
            inactive=True,
        )
        reading = read_constraints([record], axes=[("E6Base.1", Z)])

        assert reading.joints == ()
        assert any("deactivated" in note for note in reading.notes)


class TestWhatItRefusesToInvent:
    def test_a_joint_with_no_axis_is_unresolved_not_declared_along_z(self) -> None:
        """**The finding this rests on.** `GetConstraintVisuLocation` returns cleanly and
        answers a ZERO VECTOR — it is the glyph's location, not the joint's axis. A zero
        vector is not a direction, so the axis is genuinely absent from the constraint, and
        defaulting to +Z would put a plausible wrong number into a mechanism."""
        reading = read_constraints([coincidence()])

        assert reading.joints == ()
        assert len(reading.unresolved) == 1
        assert "no axis was supplied" in reading.unresolved[0]

    def test_the_refusal_names_the_geometry_the_axis_must_come_from(self) -> None:
        """So the message is actionable: the axis is resolvable from the geometry the
        constraint names, and this says which geometry that is."""
        reading = read_constraints([coincidence()])

        assert "Plan xy" in reading.unresolved[0]

    def test_an_unparseable_operand_refuses_rather_than_dropping_the_constraint(
        self,
    ) -> None:
        """Silently skipping it would produce a mechanism missing a joint, which is a
        different machine that still solves."""
        record = ConstraintRecord(
            name="Coïncidence.9",
            type_code=2,
            elements=(ConstraintElement("nonsense"), ConstraintElement(ARM_XY)),
        )
        reading = read_constraints([record], axes=[("E6Arm.1", Z)])

        assert reading.joints == ()
        assert "did not parse" in reading.unresolved[0]

    def test_an_unknown_constraint_type_is_named_not_assumed(self) -> None:
        record = ConstraintRecord(
            name="Mystère.1", type_code=99, elements=coincidence().elements
        )
        reading = read_constraints([record], axes=[("E6Base.1", Z)])

        assert reading.joints == ()
        assert "99" in reading.unresolved[0]


class TestTheTypeCodesAreCatiasOwn:
    @pytest.mark.parametrize(
        ("code", "name"),
        [(0, "fix"), (1, "offset"), (2, "coincidence"), (4, "tangency"),
         (6, "angle"), (8, "parallelism"), (11, "perpendicularity")],
    )
    def test_the_measured_codes_are_carried(self, code: int, name: str) -> None:
        """Read back from the names CATIA assigned on a French seat — `Coïncidence`,
        `Décalage`, `Fixe`. The **codes** are not localised and the names are, which is
        why this keys on the code."""
        assert CONSTRAINT_TYPES[code] == name

    def test_the_gaps_are_real(self) -> None:
        """3, 5, 7, 9, 10 and 12-15 were refused for two planes on the seat. They are
        absent rather than guessed, so a constraint carrying one is reported unknown."""
        for code in (3, 5, 7, 9, 10, 12, 13, 14, 15):
            assert code not in CONSTRAINT_TYPES
