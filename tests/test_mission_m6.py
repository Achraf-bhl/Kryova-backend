"""M6 — the belt conveyor. The ladder's first rung whose difficulty is *length*.

M1 proved a spec compiles and builds. M2 proved several parts fit together. M3
proved a part can exist twice — as a solid and as a blank — and still be one
object. M6 is the first rung where the interesting number is a **count**, and
where the failure it exists to catch is a bill of materials that stopped matching
the drawing.

Nothing in a conveyor is geometrically hard: angle-section rails, a leg pair
every so often, a roller every so often. What is hard is that the counts are a
*consequence* of the length rather than numbers somebody typed, and that changing
the length must change the bill of materials without anybody editing it. A 12-metre
conveyor built to a 6-metre BOM is the real-world failure, it is arithmetic rather
than geometry, and the only way to see it is to compute the mass twice from
different directions and insist the two agree.

Three parts, and the split is the one `app/design/` keeps everywhere.

* **The arithmetic, written out independently** — every count and every mass
  recomputed here from the drawing, never read from `missions.py`. A test that
  derives its expectation from the thing it checks agrees with any mistake that
  thing makes.
* **The conveyor itself** — built on OCCT at three lengths, with the graph, the
  closed form and the roll-up all required to move together.
* **The conveyor built wrong** — one break per guard. A mission that cannot fail
  proves nothing.

The kernel is imported inside the tests that need it, as `test_mission_m2.py`
does, so collecting this file does not drag ~166 MB of OCP into every run.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from app.design import missions as m
from app.design.errors import SpecError
from app.design.missions import Mission, MissionOutcome, MissionResult, mission, run_mission

# --------------------------------------------------------------------------
# The arithmetic, written out independently.
# --------------------------------------------------------------------------

SECTION_AREA_MM2 = 60.0 * 40.0 - 52.0 * 32.0  # RHS 60x40x4, the same section M2 uses
DENSITY_KG_M3 = 7870.0  # steel-1018
DENSITY_KG_MM3 = DENSITY_KG_M3 * 1e-9

LENGTH_MM = 6_000.0
BELT_MM = 600.0
LEG_MM = 800.0
LEG_PITCH_MM = 1_500.0
ROLLER_PITCH_MM = 500.0

ROLLER_AREA_MM2 = math.pi * (25.0**2 - 22.0**2)  # Ø50 tube, 3 mm wall


def expected_counts(length_mm: float = LENGTH_MM) -> dict[str, int]:
    """The BOM, counted by hand from the drawing.

    Both fencepost counts written out longhand rather than reused: `bays + 1` leg
    pairs and `gaps + 1` rollers are exactly the two places a hand-written BOM is
    wrong once, and copying the formula from the code under test would reproduce
    the error rather than catch it.
    """
    bays = math.ceil(length_mm / LEG_PITCH_MM)
    roller_gaps = math.ceil(length_mm / ROLLER_PITCH_MM)
    return {
        "bays": bays,
        "leg_pairs": bays + 1,
        "legs": 2 * bays + 2,
        "stringers": 2,
        "rollers": roller_gaps + 1,
    }


def expected_mass_kg(length_mm: float = LENGTH_MM) -> float:
    counts = expected_counts(length_mm)
    return (
        2.0 * SECTION_AREA_MM2 * length_mm * DENSITY_KG_MM3
        + counts["legs"] * SECTION_AREA_MM2 * LEG_MM * DENSITY_KG_MM3
        + counts["rollers"] * ROLLER_AREA_MM2 * BELT_MM * DENSITY_KG_MM3
    )


def _variant(**overrides: Any) -> Mission:
    """M6, built to a different drawing. The break harness.

    Carries M6's own assertions, so what is tested is whether the *shipped*
    claims catch the fault — not whether a claim written for the occasion does.
    """
    return Mission(
        rung="M6",
        title="Belt conveyor system",
        era="V",
        hard="Long assemblies, standard parts, modularity, layout",
        assembly=m._m6_design(**overrides),
        assertions=m._M6_ASSERTIONS,
        unproven=m._M6_UNPROVEN,
    )


def _run(rung: Mission | None = None) -> MissionResult:
    from app.kernel import OcctRunner

    return run_mission(rung or mission("M6"), runner_factory=OcctRunner)


def _failed_claims(result: MissionResult) -> set[str]:
    if result.checks is None:
        return set()
    return {check.name for check in result.checks.failed}


class TestTheCountsAreDerived:
    def test_the_shipped_counts_match_the_drawing(self) -> None:
        assert m.m6_counts() == expected_counts()

    def test_doubling_the_length_moves_every_count_that_should_move(self) -> None:
        """The rung in one test. A conveyor twice as long has twice the bays and
        the *same* two stringers, and a BOM that got either wrong is a quotation
        that is wrong."""
        short = m.m6_counts(length_mm=LENGTH_MM)
        long = m.m6_counts(length_mm=LENGTH_MM * 2)

        assert long["bays"] == short["bays"] * 2
        assert long["stringers"] == short["stringers"] == 2
        assert long["legs"] > short["legs"]
        assert long["rollers"] > short["rollers"]

    def test_the_leg_fencepost(self) -> None:
        """`n` bays need `n + 1` leg pairs. The mistake every hand-written BOM
        makes once, which is why it is asserted rather than trusted."""
        counts = m.m6_counts(length_mm=LEG_PITCH_MM * 4)

        assert counts["bays"] == 4
        assert counts["leg_pairs"] == 5
        assert counts["legs"] == 10

    def test_the_roller_fencepost(self) -> None:
        counts = m.m6_counts(length_mm=ROLLER_PITCH_MM * 4)

        assert counts["rollers"] == 5

    def test_a_length_that_is_not_a_whole_number_of_bays_rounds_up(self) -> None:
        """Down would leave the far end unsupported, which is a conveyor that
        sags at exactly the point a drawing check would not look."""
        counts = m.m6_counts(length_mm=LEG_PITCH_MM * 3 + 10.0)

        assert counts["bays"] == 4

    def test_a_zero_pitch_is_refused_rather_than_dividing(self) -> None:
        # An infinite bill of materials, and the traceback from the division
        # would name the arithmetic rather than the mistake.
        with pytest.raises(SpecError, match="infinite bill of materials"):
            m.m6_counts(leg_pitch_mm=0.0)

    def test_the_closed_form_matches_the_drawing_at_three_lengths(self) -> None:
        for length in (3_000.0, LENGTH_MM, 12_000.0):
            assert m.m6_mass_kg(length_mm=length) == pytest.approx(
                expected_mass_kg(length), rel=1e-9
            )


class TestTheConveyorBuilds:
    def test_it_passes_on_the_real_kernel(self) -> None:
        result = _run()

        assert result.outcome is MissionOutcome.PASSED, result.reason

    def test_the_roll_up_equals_the_closed_form(self) -> None:
        """One mass from the product graph, one from the pitches. This is the
        rung: they must agree, or the BOM has drifted from the drawing."""
        result = _run()

        assert result.assembly is not None
        rolled = result.assembly.payload["mass_kg"]
        assert rolled == pytest.approx(expected_mass_kg(), rel=1e-6)

    def test_the_run_is_as_long_as_it_was_asked_to_be(self) -> None:
        result = _run()

        assert result.assembly is not None
        # The envelope is the run plus the section a stringer occupies at each
        # end — the rails themselves are exactly the run length.
        assert result.assembly.payload["envelope_mm"]["size"][0] == pytest.approx(
            LENGTH_MM + 60.0, abs=1.0
        )

    def test_the_graph_holds_exactly_what_the_bom_counts(self) -> None:
        """The BOM checked against the graph directly, beside the mass check that
        checks it by arithmetic. Two different ways of being wrong."""
        result = _run()
        counts = expected_counts()

        assert result.assembly is not None
        assert result.assembly.payload["clash"]["occurrence_count"] == (
            counts["stringers"] + counts["legs"] + counts["rollers"]
        )

    def test_it_builds_at_a_different_length_without_anybody_editing_a_count(
        self,
    ) -> None:
        """The modularity claim, driven rather than asserted. If any count were
        written out by hand this would fail, because the closed form and the
        graph would disagree."""
        result = _run(_variant(length_mm=9_000.0))

        assert result.outcome is MissionOutcome.PASSED, result.reason
        assert result.assembly is not None
        assert result.assembly.payload["mass_kg"] == pytest.approx(
            expected_mass_kg(9_000.0), rel=1e-6
        )


class TestTheConveyorBuiltWrong:
    """Every claim M6 makes, broken on purpose at least once.

    A guard nobody has seen fail is a guard nobody has verified.
    """

    def test_a_graph_built_for_the_wrong_length_fails_the_mass_claim(self) -> None:
        """**The failure this rung exists for.** The structure is built for six
        metres and the parameters say nine: exactly a conveyor quoted from a
        drawing somebody had already superseded. Nothing about the geometry is
        malformed — every part builds, every joint closes — and the only thing
        that catches it is computing the mass twice."""
        stale = m._m6_structure(length_mm=LENGTH_MM)

        result = _run(_variant(length_mm=9_000.0, structure=stale))

        assert result.outcome is not MissionOutcome.PASSED
        assert "the roll-up equals the closed form over the derived counts" in _failed_claims(
            result
        )

    def test_a_coarser_leg_pitch_changes_the_bill_and_is_noticed(self) -> None:
        """Fewer legs is a real design change and the closed form follows it, so
        this must *pass* — the guard is that the two move together, not that the
        pitch never changes."""
        result = _run(_variant(leg_pitch_mm=3_000.0))

        assert result.outcome is MissionOutcome.PASSED, result.reason

    def test_a_structure_missing_a_leg_pair_fails(self) -> None:
        """A leg pair dropped from the graph is a conveyor with an unsupported
        bay, and it shows up as mass rather than as a geometry error — which is
        why the mass claim is the one that catches it."""
        short = m._m6_structure(length_mm=LENGTH_MM - LEG_PITCH_MM)

        result = _run(_variant(length_mm=LENGTH_MM, structure=short))

        assert result.outcome is not MissionOutcome.PASSED

    def test_the_envelope_claim_catches_a_run_placed_wrong(self) -> None:
        result = _run(_variant(structure=m._m6_structure(length_mm=2_000.0)))

        assert result.outcome is not MissionOutcome.PASSED
        assert "the run is as long as it was asked to be" in _failed_claims(result)


class TestWhatM6DoesNotClaim:
    def test_the_rung_carries_its_caveats(self) -> None:
        """"M6 passed" must never be readable as "the conveyor carries the belt".
        Nothing here has run a load case."""
        rung = mission("M6")

        assert rung.unproven
        assert any("no load case" in caveat for caveat in rung.unproven)

    def test_it_does_not_claim_the_rollers_turn(self) -> None:
        """A static layout. Belt tension, drive torque and the loads a moving
        belt puts into the frame are all E9's, and all absent."""
        assert any("do not turn" in caveat for caveat in mission("M6").unproven)

    def test_it_does_not_claim_the_roller_is_orderable(self) -> None:
        """The roller is an envelope with a catalogue mass. E12.3's whole
        argument is that a bought part is *selected*, and nothing here has
        selected one."""
        assert any("part number" in caveat for caveat in mission("M6").unproven)

    def test_it_does_not_claim_a_quotation(self) -> None:
        # The bill of materials is a count of parts. A count is not a price.
        assert any("not a quotation" in caveat for caveat in mission("M6").unproven)

    def test_the_caveats_reach_the_public_gallery(self) -> None:
        """The gallery is derived from the ladder for exactly this reason — a
        page printing the passes and dropping the caveats would be the most
        misleading page in the product, because it would be the most
        convincing."""
        from app.handbook.gallery import entry_for

        entry = entry_for(mission("M6"))

        assert entry.buildable
        assert entry.not_claimed == tuple(mission("M6").unproven)
