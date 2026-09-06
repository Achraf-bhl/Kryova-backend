"""Bought-in parts that say what they do not know — master plan Phase 12.

A bolt whose mass is known but whose proof load is not **cannot be checked**, and
the record has to say so rather than hand back a plausible default. That is the
whole difference between a parts library an engineer can sign off and one that
gets somebody hurt: the second kind is easier to build, reads identically, and
fails only when a joint opens.

The design here goes further than "absent", and the extra step is what makes it
usable: every absent quantity carries **why**. Tightening torque is missing from
every bolt in the catalogue not because nobody typed it in but because it is a
property of the *joint* — it depends on thread and head friction, which varies by
a factor of three between a dry zinc-plated bolt and a lubricated one, and on the
utilisation chosen at assembly. A reason like that tells the reader what to do
next. A bare `None` tells them to go and find a number, and the number they find
will be for somebody else's joint.

Verified against ISO 898-1 arithmetic rather than against what the code returned:
a property class 8.8 bolt has a nominal proof stress of 580 MPa, so its proof
load is 580 × the tensile stress area, and an M8's stress area is 36.6 mm².
"""

from __future__ import annotations

import pytest

from app.parts import CATALOGUE, MissingEngineeringData, UnknownPart, find

M8 = "M8x40 ISO 4014 8.8"

#: ISO 898-1: the nominal stress under proof load for property class 8.8.
PROOF_STRESS_MPA_8_8 = 580.0

#: ISO 898-1 / ISO 724 tensile stress area for an M8 coarse thread.
M8_STRESS_AREA_MM2 = 36.6


class TestAPartCarriesRealData:
    def test_a_bolt_is_found_by_its_designation(self) -> None:
        part = find(M8)

        assert part.kind == "bolt"
        assert "M8" in str(part.designation)

    def test_its_geometry_is_in_millimetres(self) -> None:
        """Units are mm-N-MPa everywhere and nothing converts, so an M8's
        nominal diameter is 8, not 0.008 and not 0.315."""
        part = find(M8)

        assert part.value("nominal_diameter_mm") == pytest.approx(8.0)
        assert part.value("length_mm") == pytest.approx(40.0)

    def test_the_thread_pitch_is_the_coarse_one(self) -> None:
        """M8 coarse is 1.25 mm. Getting this wrong changes the stress area and
        therefore every load the bolt is checked against."""
        part = find(M8)

        assert part.value("thread_pitch_mm") == pytest.approx(1.25)

    def test_the_stress_area_is_the_standard_value(self) -> None:
        part = find(M8)

        assert part.value("tensile_stress_area_mm2") == pytest.approx(
            M8_STRESS_AREA_MM2, rel=0.01
        )

    def test_the_proof_load_is_the_stress_area_times_the_proof_stress(self) -> None:
        """ISO 898-1 arithmetic, done here rather than trusted from the record.

        A proof load that does not equal As × 580 for an 8.8 is either the wrong
        property class or the wrong stress area, and both are the kind of error
        that produces a believable number.
        """
        part = find(M8)

        expected = M8_STRESS_AREA_MM2 * PROOF_STRESS_MPA_8_8
        assert part.value("proof_load_n") == pytest.approx(expected, rel=0.02)

    def test_the_property_class_strengths_are_the_8_8_ones(self) -> None:
        """8.8 means 800 MPa ultimate and 80% of it at yield — the two digits
        are the specification, not a name."""
        part = find(M8)

        assert part.value("ultimate_tensile_strength_mpa") == pytest.approx(800.0, rel=0.01)
        assert part.value("yield_strength_mpa") == pytest.approx(640.0, rel=0.01)


class TestWhatItDoesNotKnowItSaysLoudly:
    def test_asking_for_an_absent_quantity_raises(self) -> None:
        """Not `None`. A caller who gets `None` writes `or 0.0` and carries on;
        a caller who gets an exception has to decide."""
        part = find(M8)

        with pytest.raises(MissingEngineeringData):
            part.require("tightening_torque_n_mm")

    def test_the_refusal_names_the_quantity(self) -> None:
        part = find(M8)

        with pytest.raises(MissingEngineeringData) as refused:
            part.require("tightening_torque_n_mm")

        assert "tightening_torque_n_mm" in str(refused.value)

    def test_the_refusal_says_why_it_is_absent(self) -> None:
        """The half that makes it actionable. Tightening torque is a property of
        the joint rather than of the bolt, and a reader told that goes and picks
        a friction coefficient instead of going to look for a number that does
        not exist."""
        part = find(M8)

        with pytest.raises(MissingEngineeringData) as refused:
            part.require("tightening_torque_n_mm")

        message = str(refused.value)
        assert "friction" in message.lower()

    def test_the_refusal_says_what_the_record_does_hold(self) -> None:
        """So the caller can see whether a different check is available without
        going back to the catalogue."""
        part = find(M8)

        with pytest.raises(MissingEngineeringData) as refused:
            part.require("fatigue_strength_mpa")

        assert "proof_load_n" in str(refused.value)

    def test_every_absent_quantity_carries_a_reason(self) -> None:
        """An absent quantity with no reason is a gap in the record rather than
        an honest absence, and the two must not be allowed to look alike."""
        for part in CATALOGUE:
            for name, reason in part.absent.items():
                assert reason.strip(), (
                    f"{part.designation} declares {name} absent with no reason; "
                    "an absence nobody explained is indistinguishable from an "
                    "omission nobody noticed"
                )

    def test_value_returns_none_where_require_raises(self) -> None:
        """The two accessors are for different callers: one that can carry on
        without the number, and one that cannot."""
        part = find(M8)

        assert part.value("tightening_torque_n_mm") is None

    def test_missing_engineering_lists_what_makes_it_uncheckable(self) -> None:
        """Non-empty is not a defect in the record — it is the record being
        honest — but it does mean a check involving those quantities must refuse
        rather than run."""
        part = find(M8)

        missing = part.missing_engineering()

        for name in missing:
            assert part.get(name) is None


class TestAnUnknownPartIsRefused:
    def test_a_designation_nobody_holds_raises(self) -> None:
        with pytest.raises((UnknownPart, LookupError)):
            find("M8x40 ISO 9999 12.9")

    def test_the_catalogue_is_not_empty(self) -> None:
        """A catalogue that silently holds nothing would make every `find` a
        refusal and look exactly like a catalogue that is working."""
        assert len(CATALOGUE) > 0


class TestTighteningTorqueIsComputedNotLookedUp:
    """Because it depends on the friction the caller is prepared to defend."""

    def test_it_needs_a_friction_coefficient(self) -> None:
        from app.parts import fasteners

        assert hasattr(fasteners, "tightening_torque")

    def test_two_frictions_give_two_torques(self) -> None:
        """The factor-of-three spread between a dry zinc-plated bolt and a
        lubricated one is the reason this is not a table."""
        from app.parts.fasteners import tightening_torque

        part = find(M8)
        dry = tightening_torque(part, thread_friction=0.20)
        lubricated = tightening_torque(part, thread_friction=0.08)

        assert dry.value != lubricated.value
        # And the direction, which is the physics rather than an artefact: more
        # friction means more of the torque is spent overcoming it, so a dry
        # bolt needs MORE torque to reach the same preload. A sign error here
        # under-tightens every lubricated joint.
        assert dry.value > lubricated.value
