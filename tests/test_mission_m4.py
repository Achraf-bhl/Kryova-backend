"""M4 — the gearbox. The ladder's first rung that is arithmetic before it is a shape.

M1 proved a spec compiles and builds. M2 proved several parts fit together. M3
proved a part can exist twice — as a solid and as a blank — and still be one
object. M6 proved a bill of materials can be a *consequence* of a dimension. M4
is the first rung where the thing that decides whether the machine works is not a
dimension at all: two gears mesh when the centre distance is `m(z1 + z2)/2`, and
nothing about either solid knows that number exists.

So this rung checks three claims that have to agree, and only one of them is
geometry:

* **the mesh**, declared as an `Interface` both gears are built from and closed by
  a measurement *between the built solids* — the gap between the two root
  cylinders is `2.5 x module` exactly when the centre distance is right;
* **the axial chain**, five dimensions and five tolerances closing on an end float
  that must not close, checked by `app/rules/stackup.py`'s worst case and asserted
  against the built housing rather than the drawing it was computed from;
* **the bought parts**, which the product declines to size: every bearing that
  fits the output shaft is in the shipped table with its ISO 15 boundary
  dimensions and no load rating, so `select` refuses and the rung carries the
  refusal instead of a life.

Four parts, the split `app/design/` keeps everywhere.

* **The tooth system, written out independently** — every diameter re-derived here
  from module and tooth count, never read from `missions.py`. A test that takes
  its expectation from the thing it checks agrees with any mistake that thing makes.
* **The gearbox itself** — built on OCCT, measured, and required to agree with the
  arithmetic in four separate directions.
* **The gearbox built wrong** — one break per guard. A mission that cannot fail
  proves nothing.
* **The rung in the ladder** — that it says what it does not claim, and that the
  two things it cannot do reach the public gallery.

**These tests were written on Linux on 2026-09-16 and were not run as pytest**,
at the user's instruction. Every number asserted below was measured first, by
building the real gearbox through the real OCCT kernel with a one-off script: the
mass, the centre of mass, the envelope, the measured mesh gap and every break's
failing claim are transcriptions of that run, not predictions of it.

The kernel is imported inside the tests that need it, as `test_mission_m2.py`
does, so collecting this file does not drag ~166 MB of OCP into every run.
"""

from __future__ import annotations

import math

import pytest

from app.design import missions as m
from app.design.errors import SpecError
from app.design.missions import Mission, MissionResult, mission, run_mission
from app.rules.stackup import Method, Risk

# --------------------------------------------------------------------------
# The tooth system, written out independently.
# --------------------------------------------------------------------------

MODULE_MM = 3.0
PINION_TEETH = 24
WHEEL_TEETH = 48
FACE_WIDTH_MM = 30.0

# Standard full-depth proportions: addendum = m, dedendum = 1.25 m. Written out
# rather than imported, because these two constants are the whole tooth system
# and reusing the code's copy of them would make every diameter below a tautology.
PINION_PITCH_MM = MODULE_MM * PINION_TEETH        # 72.0
WHEEL_PITCH_MM = MODULE_MM * WHEEL_TEETH          # 144.0
CENTRE_DISTANCE_MM = (PINION_PITCH_MM + WHEEL_PITCH_MM) / 2.0   # 108.0
PINION_ROOT_MM = PINION_PITCH_MM - 2.5 * MODULE_MM              # 64.5
WHEEL_ROOT_MM = WHEEL_PITCH_MM - 2.5 * MODULE_MM                # 136.5
PINION_TIP_MM = PINION_PITCH_MM + 2.0 * MODULE_MM               # 78.0
WHEEL_TIP_MM = WHEEL_PITCH_MM + 2.0 * MODULE_MM                 # 150.0
ROOT_CLEARANCE_MM = 2.5 * MODULE_MM                             # 7.5

DENSITY_KG_M3 = 7870.0  # steel-1018
DENSITY_KG_MM3 = DENSITY_KG_M3 * 1e-9

INPUT_SHAFT_MM = 25.0
OUTPUT_SHAFT_MM = 35.0
INPUT_BEARING_OD_MM, INPUT_BEARING_W_MM = 47.0, 12.0    # 6005, ISO 15 Table 4
OUTPUT_BEARING_OD_MM, OUTPUT_BEARING_W_MM = 62.0, 14.0  # 6007, ISO 15 Table 4

SEAT_SPAN_MM = 80.0
END_FLOAT_MM = 4.0
COVER_MM = 12.0
WALL_MM = 15.0
RADIAL_CLEARANCE_MM = 10.0
SHAFT_PROTRUSION_MM = 48.0
SHAFT_LENGTH_MM = SEAT_SPAN_MM + 2.0 * COVER_MM + SHAFT_PROTRUSION_MM  # 152.0
OVERALL_LENGTH_MM = SHAFT_LENGTH_MM + SHAFT_PROTRUSION_MM              # 200.0

CAVITY_HEIGHT_MM = 2.0 * (CENTRE_DISTANCE_MM / 2.0 + WHEEL_TIP_MM / 2.0 + RADIAL_CLEARANCE_MM)
CAVITY_WIDTH_MM = 2.0 * (WHEEL_TIP_MM / 2.0 + RADIAL_CLEARANCE_MM)
HOUSING_HEIGHT_MM = CAVITY_HEIGHT_MM + 2.0 * WALL_MM   # 308.0
HOUSING_WIDTH_MM = CAVITY_WIDTH_MM + 2.0 * WALL_MM     # 200.0

INPUT_SPACER_MM = SEAT_SPAN_MM - END_FLOAT_MM - 2.0 * INPUT_BEARING_W_MM - FACE_WIDTH_MM
OUTPUT_SPACER_MM = SEAT_SPAN_MM - END_FLOAT_MM - 2.0 * OUTPUT_BEARING_W_MM - FACE_WIDTH_MM
INPUT_SPACER_OD_MM = 32.0
OUTPUT_SPACER_OD_MM = 45.0


def annulus_mm2(outer_mm: float, bore_mm: float) -> float:
    return math.pi / 4.0 * (outer_mm**2 - bore_mm**2)


def expected_mass_kg() -> float:
    """The gearbox weighed by hand, part by part, from the drawing.

    Thirteen occurrences: one housing, two covers, two gears, two shafts, four
    bearings and two spacers. Written out longhand rather than looped, because the
    failure this is here to catch is a part counted once too often or not at all —
    and a loop over a table would be the same loop the code under test runs.
    """
    cover_bore_in = INPUT_SHAFT_MM + 2.0
    cover_bore_out = OUTPUT_SHAFT_MM + 2.0
    housing = (
        HOUSING_WIDTH_MM * HOUSING_HEIGHT_MM - CAVITY_WIDTH_MM * CAVITY_HEIGHT_MM
    ) * SEAT_SPAN_MM
    cover = (
        HOUSING_WIDTH_MM * HOUSING_HEIGHT_MM
        - math.pi / 4.0 * cover_bore_in**2
        - math.pi / 4.0 * cover_bore_out**2
    ) * COVER_MM
    pinion = annulus_mm2(PINION_ROOT_MM, INPUT_SHAFT_MM) * FACE_WIDTH_MM
    wheel = annulus_mm2(WHEEL_ROOT_MM, OUTPUT_SHAFT_MM) * FACE_WIDTH_MM
    input_shaft = math.pi / 4.0 * INPUT_SHAFT_MM**2 * SHAFT_LENGTH_MM
    output_shaft = math.pi / 4.0 * OUTPUT_SHAFT_MM**2 * SHAFT_LENGTH_MM
    input_bearing = annulus_mm2(INPUT_BEARING_OD_MM, INPUT_SHAFT_MM) * INPUT_BEARING_W_MM
    output_bearing = annulus_mm2(OUTPUT_BEARING_OD_MM, OUTPUT_SHAFT_MM) * OUTPUT_BEARING_W_MM
    input_spacer = annulus_mm2(INPUT_SPACER_OD_MM, INPUT_SHAFT_MM) * INPUT_SPACER_MM
    output_spacer = annulus_mm2(OUTPUT_SPACER_OD_MM, OUTPUT_SHAFT_MM) * OUTPUT_SPACER_MM

    volume = (
        housing
        + 2.0 * cover
        + pinion
        + wheel
        + input_shaft
        + output_shaft
        + 2.0 * input_bearing
        + 2.0 * output_bearing
        + input_spacer
        + output_spacer
    )
    return volume * DENSITY_KG_MM3


def _variant(**overrides: object) -> Mission:
    """M4, built to a different drawing. The break harness.

    Carries M4's own assertions, so what is tested is whether the *shipped* claims
    catch the fault — not whether a claim written for the occasion does.
    """
    return Mission(
        rung="M4",
        title="Gearbox",
        era="IV",
        hard="Gear geometry, bearings, tolerance stacks, lubrication",
        assembly=m._m4_design(**overrides),  # type: ignore[arg-type]
        assertions=m._M4_ASSERTIONS,
        unproven=m._M4_UNPROVEN,
    )


def _run(rung: Mission | None = None) -> MissionResult:
    from app.kernel import OcctRunner

    return run_mission(rung or mission("M4"), runner_factory=OcctRunner)


def _failed_claims(result: MissionResult) -> set[str]:
    if result.checks is None:
        return set()
    return {check.name for check in result.checks.failed}


def _violated_claims(result: MissionResult) -> set[str]:
    if result.assembly is None:
        return set()
    return {violation.claim for violation in result.assembly.violations}


class TestTheToothSystemIsArithmetic:
    """Every diameter a spur gear has is the module times a function of its teeth."""

    def test_the_pair_is_the_one_the_drawing_specifies(self) -> None:
        pair = m.m4_gear_pair()

        assert pair["module_mm"] == MODULE_MM
        assert pair["centre_distance_mm"] == CENTRE_DISTANCE_MM
        assert pair["ratio"] == WHEEL_TEETH / PINION_TEETH
        assert pair["pinion_pitch_diameter_mm"] == PINION_PITCH_MM
        assert pair["wheel_pitch_diameter_mm"] == WHEEL_PITCH_MM
        assert pair["pinion_root_diameter_mm"] == PINION_ROOT_MM
        assert pair["wheel_root_diameter_mm"] == WHEEL_ROOT_MM
        assert pair["pinion_tip_diameter_mm"] == PINION_TIP_MM
        assert pair["wheel_tip_diameter_mm"] == WHEEL_TIP_MM

    def test_the_root_clearance_is_two_and_a_half_modules_whatever_the_teeth(self) -> None:
        """The reason this rung measures that gap rather than the centre distance.

        `a - r_f1 - r_f2` is `m(z1+z2)/2 - m(z1-2.5)/2 - m(z2-2.5)/2`, and the
        tooth counts cancel: the answer is `2.5 m` for every pair that meshes. So
        one measurement between two built solids says the centre distance is right
        without knowing which gears are in front of it.
        """
        for teeth in ((24, 48), (17, 51), (12, 12), (31, 97)):
            for module in (1.0, 2.5, 3.0, 8.0):
                pair = m.m4_gear_pair(
                    module_mm=module, pinion_teeth=teeth[0], wheel_teeth=teeth[1]
                )
                assert pair["root_clearance_mm"] == pytest.approx(2.5 * module)

    def test_the_tip_circles_always_overlap_by_two_modules(self) -> None:
        """Why the gears are modelled as root cylinders and not as tip cylinders.

        A gear pair drawn at its tip diameter interferes **by construction** at the
        correct centre distance, so a clash check over tip cylinders reports a clash
        on a gearbox that is right, and there is no threshold that fixes it — the
        teeth really do reach into each other's space, which is what meshing is.
        """
        for module in (1.0, 3.0, 6.0):
            pair = m.m4_gear_pair(module_mm=module)
            assert pair["tip_overlap_mm"] == pytest.approx(2.0 * module)
            assert pair["tip_overlap_mm"] > 0.0

    def test_a_module_of_zero_is_refused(self) -> None:
        with pytest.raises(SpecError, match="positive length"):
            m.m4_gear_pair(module_mm=0.0)

    def test_a_gear_with_no_teeth_is_refused(self) -> None:
        """Not a guard against silliness: `m(z - 2.5)` at z = 0 is a *negative*
        root diameter, which a circle operation takes and a pad builds."""
        with pytest.raises(SpecError, match="at least one tooth"):
            m.m4_gear_pair(pinion_teeth=0)


class TestTheAxialChain:
    """Five dimensions closing on an end float that must not close."""

    def test_the_chain_is_the_five_dimensions_on_the_output_shaft(self) -> None:
        names = [c.name for c in m.m4_axial_chain()]

        assert names == [
            "housing seat span",
            "bearing width, cover side",
            "spacer sleeve",
            "gear hub width",
            "bearing width, drive side",
        ]

    def test_only_the_seat_span_opens_the_gap(self) -> None:
        """The sign convention is the whole of it. Written the other way round the
        arithmetic still runs and the stack closes on 156 mm, which is not a number
        anybody would query."""
        chain = m.m4_axial_chain()

        assert chain[0].nominal_mm > 0
        assert all(c.nominal_mm < 0 for c in chain[1:])

    def test_the_nominal_end_float_is_what_the_layout_leaves(self) -> None:
        verdict = m.m4_end_float()

        assert verdict.result.nominal_mm == pytest.approx(END_FLOAT_MM)

    def test_the_float_never_closes_at_worst_case(self) -> None:
        """The claim. A stack that goes negative is not a tight fit — it is a pair
        of bearings clamped axially through their balls, and that is a failure
        measured in months rather than in millimetres."""
        verdict = m.m4_end_float()

        assert verdict.passed
        assert verdict.result.method is Method.WORST_CASE
        assert verdict.at_least_mm == 0.0
        assert verdict.result.minimum_mm == pytest.approx(3.68)
        assert verdict.result.maximum_mm == pytest.approx(4.32)

    def test_it_is_worst_case_and_a_statistical_band_is_not_available_anonymously(
        self,
    ) -> None:
        """Why this rung does not print an RSS number, which would be 42% narrower.

        `stackup.stack` refuses a statistical band without a name against the
        independence assumption, and this chain is the case for that refusal: two
        of its five contributors are widths off the same bearing, which is exactly
        the correlation RSS assumes away.
        """
        from app.rules.stackup import stack

        refused = stack(m.m4_axial_chain(), method=Method.STATISTICAL)

        assert not refused.available
        assert refused.refusals
        assert any(Risk.INDEPENDENCE.value in word for word in refused.refusals) or any(
            "independen" in word.lower() for word in refused.refusals
        )

    def test_every_contributor_says_where_its_tolerance_came_from(self) -> None:
        """And what it says is *this drawing*, never a standard nobody read.

        ISO 492's bearing width deviations are not transcribed anywhere in this
        repository. A figure quietly attributed to it would be the benchmark-target
        failure `app/verify/nafems.py` refuses, one level down.
        """
        for contributor in m.m4_axial_chain():
            assert contributor.source
            assert "M4 drawing" in contributor.source
        bearings = [c for c in m.m4_axial_chain() if "bearing" in c.name]
        assert len(bearings) == 2
        assert all("ISO 492" in c.source for c in bearings)


class TestTheBoughtPartsAreBoughtAndTheProductSaysSo:
    def test_the_bearings_come_from_the_shipped_catalogue(self) -> None:
        """Looked up, not typed: the boundary dimensions this rung places carry
        ISO 15 as their source, and a change to the shipped table moves the
        gearbox rather than silently disagreeing with it."""
        assert m._M4_INPUT_BEARING_PART.designation == "6005"
        assert m._M4_INPUT_BEARING_PART.outer_diameter_mm == INPUT_BEARING_OD_MM
        assert m._M4_INPUT_BEARING_PART.width_mm == INPUT_BEARING_W_MM
        assert m._M4_OUTPUT_BEARING_PART.designation == "6007"
        assert m._M4_OUTPUT_BEARING_PART.outer_diameter_mm == OUTPUT_BEARING_OD_MM
        assert m._M4_OUTPUT_BEARING_PART.width_mm == OUTPUT_BEARING_W_MM
        assert m._M4_INPUT_BEARING_PART.source is not None
        assert "ISO 15" in m._M4_INPUT_BEARING_PART.source.citation

    def test_a_bearing_that_does_not_fit_its_shaft_is_refused(self) -> None:
        """The failure is silent otherwise: a designation typed one digit out is a
        bearing that exists, has plausible dimensions and does not fit."""
        with pytest.raises(SpecError, match="does not fit its shaft"):
            m._m4_bearing("6005", 35.0)

    def test_the_product_declines_to_size_the_bearing_and_says_why(self) -> None:
        """**This rung's finding, and it is a refusal rather than a number.**

        `select` considers every 6-series bearing that fits a 35 mm shaft and
        refuses all of them: not one carries `C` or `C0`, because a load rating is
        the maker's number and differs between makers for the same ISO envelope.
        A mission that skipped the call would be a gearbox whose bearings nobody
        had even asked about.
        """
        from app.parts.bearings import Refusal

        verdict = m.m4_bearing_verdict()

        assert isinstance(verdict, Refusal)
        assert verdict.considered >= 3
        assert any("6007" in candidate for candidate in verdict.unselectable)
        assert all("load rating" in candidate for candidate in verdict.unselectable)

    def test_the_gears_are_blanks_and_the_missing_metal_is_an_exact_bound(self) -> None:
        """Not an estimate of the teeth. The real pair sits between the root
        cylinders built here and the tip cylinders; this is the whole of the
        difference, so the gearbox's true mass is above what it reports by at most
        this much and nothing pretends to know where in that band it falls."""
        pinion = annulus_mm2(PINION_TIP_MM, PINION_ROOT_MM) * FACE_WIDTH_MM
        wheel = annulus_mm2(WHEEL_TIP_MM, WHEEL_ROOT_MM) * FACE_WIDTH_MM

        assert m.m4_tooth_volume_not_modelled_mm3() == pytest.approx(pinion + wheel)
        assert m.m4_tooth_volume_not_modelled_mm3() == pytest.approx(136_459.0, abs=1.0)


class TestTheClosedForm:
    def test_the_mass_is_the_thirteen_occurrences_weighed_by_hand(self) -> None:
        assert m.m4_mass_kg() == pytest.approx(expected_mass_kg(), rel=1e-12)
        assert m.m4_mass_kg() == pytest.approx(26.8053, abs=1e-4)

    def test_the_weight_hangs_below_the_mesh_centreline(self) -> None:
        """The wheel's blank is nearly five times the pinion's and it is the part
        that hangs. This is the one number in the rung that knows which way up the
        machine is."""
        assert m.m4_centre_of_mass_z_mm() < 0.0
        assert m.m4_centre_of_mass_z_mm() == pytest.approx(-6.6361, abs=1e-3)

    def test_the_bill_of_materials_is_thirteen_occurrences_of_ten_parts(self) -> None:
        components = {name for name, _ in m._M4_OCCURRENCE_AXES}

        assert len(m._M4_OCCURRENCE_AXES) == 13
        assert len(components) == 10


class TestTheGearboxBuilds:
    """Built on the real kernel, and required to agree with the arithmetic."""

    def test_it_passes(self) -> None:
        result = _run()

        assert result.passed, result.reason

    def test_the_roll_up_equals_the_closed_form(self) -> None:
        """The rung, in one number. Thirteen occurrences of ten different parts,
        weighed once by the product graph and once by arithmetic — so a part placed
        twice, or a diameter changed in one of the two places it is written, is red
        rather than a gearbox that is quietly 2 kg out."""
        result = _run()
        assert result.assembly is not None

        assert result.assembly.payload["mass_kg"] == pytest.approx(
            expected_mass_kg(), rel=1e-6
        )

    def test_the_mesh_contract_holds_and_the_gap_was_measured(self) -> None:
        """The centre distance, read off the built solids rather than computed from
        the placement that put them there."""
        result = _run()
        assert result.assembly is not None
        contracts = {c.interface.name: c for c in result.assembly.contracts}

        mesh = contracts["spur gear mesh"]
        assert mesh.ok, mesh.report.summary()
        measured = {r.assertion.measure: r.measured for r in mesh.report.results}
        assert measured["minimum_clearance_mm"] == pytest.approx(ROOT_CLEARANCE_MM, abs=1e-3)
        assert measured["provider.bounding_box_mm.size[0]"] == pytest.approx(
            PINION_ROOT_MM, abs=1e-3
        )
        assert measured["consumer.bounding_box_mm.size[0]"] == pytest.approx(
            WHEEL_ROOT_MM, abs=1e-3
        )

    def test_nothing_occupies_the_same_space_as_anything_else(self) -> None:
        """Thirty-six of the seventy-eight pairs are close enough to be worth
        measuring and the other forty-two are separated by their bounding boxes;
        none of them interferes. The parts that touch — bearing, spacer, hub — read
        as zero clearance and not as a clash, which is what touching is."""
        result = _run()
        assert result.assembly is not None
        clash = result.assembly.payload["clash"]

        assert clash["occurrence_count"] == 13
        assert clash["clash_count"] == 0
        assert clash["interferes"] is False
        assert clash["complete"] is True
        assert clash["unchecked_pair_count"] == 0
        assert clash["minimum_clearance_mm"] == pytest.approx(0.0, abs=1e-6)

    def test_the_machine_is_the_size_it_was_drawn(self) -> None:
        """200 long, 200 across, 308 tall. The length is **not** the shaft length:
        the two shafts protrude at opposite ends, so the machine is one shaft plus
        one more protrusion — which was written as the shaft length first, and this
        claim is what caught it."""
        result = _run()
        assert result.assembly is not None
        size = result.assembly.payload["envelope_mm"]["size"]

        assert size[0] == pytest.approx(OVERALL_LENGTH_MM, abs=1e-3)
        assert size[1] == pytest.approx(HOUSING_WIDTH_MM, abs=1e-3)
        assert size[2] == pytest.approx(HOUSING_HEIGHT_MM, abs=1e-3)

    def test_the_housing_clears_the_wheel_by_the_radial_clearance(self) -> None:
        """The cavity is derived from the tip diameters, not typed. A housing sized
        by hand stops clearing the wheel the day the ratio changes, and the wheel
        is the part that grows."""
        half_cavity = CAVITY_HEIGHT_MM / 2.0
        wheel_reach = CENTRE_DISTANCE_MM / 2.0 + WHEEL_TIP_MM / 2.0

        assert half_cavity - wheel_reach == pytest.approx(RADIAL_CLEARANCE_MM)
        assert m._M4_CAVITY_HEIGHT_MM == pytest.approx(CAVITY_HEIGHT_MM)

    def test_the_seat_span_leaves_room_for_the_widest_stack_the_drawing_allows(
        self,
    ) -> None:
        """The tolerance stack, read off the built housing. Bearing, spacer, hub and
        bearing all at their upper limits inside a seat span at its lower one."""
        result = _run()
        assert result.assembly is not None
        housing = result.assembly.payload["housing"]["bounding_box_mm"]["size"][2]

        assert housing == pytest.approx(SEAT_SPAN_MM, abs=1e-3)
        assert housing >= 76.42

    def test_the_centre_of_mass_is_where_the_arithmetic_puts_it(self) -> None:
        result = _run()
        assert result.assembly is not None
        centre = result.assembly.payload["centre_of_mass_mm"]

        assert centre[1] == pytest.approx(0.0, abs=1e-6)
        assert centre[2] == pytest.approx(m.m4_centre_of_mass_z_mm(), abs=1e-4)
        assert centre[2] < 0.0


class TestTheGearboxBuiltWrong:
    """One break per guard. A mission that cannot fail proves nothing.

    Each case below was run on the real kernel on 2026-09-16 and the claim named
    in each test is the one that actually came back failed.
    """

    def test_a_shaft_bored_a_millimetre_out_fails_the_mesh(self) -> None:
        """The failure this rung exists for. Every part is right, every mass is
        right, nothing interferes — and the gears do not mesh, because the centre
        distance is not `m(z1 + z2)/2`. A millimetre on a 108 mm centre distance
        is a gearbox that binds."""
        result = _run(
            _variant(
                structure=m._m4_structure(
                    centre_distance_mm=CENTRE_DISTANCE_MM + 1.0
                )
            )
        )

        assert not result.passed
        assert (
            "the blanks stand 2.5 modules apart, so the centre distance is right"
            in _violated_claims(result)
        )

    def test_assembling_it_upside_down_is_caught_twice(self) -> None:
        """Swap the two shafts and every mass, diameter and clearance in the rung is
        unchanged. Two claims still move: the centre of mass, which is the one number
        that knows which way up the machine is, and the clash check — because the
        covers are bored Ø27 high and Ø37 low, so the 35 mm output shaft now runs
        through solid plate."""
        result = _run(_variant(structure=m._m4_structure(upside_down=True)))

        assert not result.passed
        failed = _failed_claims(result)
        assert "the weight hangs below the mesh centreline" in failed
        assert "nothing in the gearbox occupies the same space as anything else" in failed

    def test_a_part_in_the_graph_that_the_bill_of_materials_does_not_count(self) -> None:
        """M6's pair of counting claims, on a machine whose parts are all different.
        The spare bearing sits on the protruding output shaft where it fouls
        nothing, so the *only* things that notice are the two counts."""
        result = _run(_variant(structure=m._m4_structure(spare_bearing_at_mm=100.0)))

        assert not result.passed
        failed = _failed_claims(result)
        assert "the graph holds exactly the parts the bill of materials counts" in failed
        assert "the roll-up equals the closed form over every occurrence" in failed

    def test_a_housing_machined_shorter_than_the_stack_was_closed_on(self) -> None:
        """76 mm of seat span against a worst-case stack of 76.42 mm. The drawing's
        arithmetic is untouched and only the metal moved, which is exactly the case
        a stack computed once and never re-checked is blind to."""
        result = _run(_variant(seat_span_mm=76.0))

        assert not result.passed
        assert (
            "the seat span leaves room for the widest stack the drawing allows"
            in _failed_claims(result)
        )

    def test_the_intact_gearbox_still_passes_after_all_of_that(self) -> None:
        """Every break above goes through `_m4_design`'s own arguments rather than
        through a mutated module, so there is nothing to restore — and this asserts
        it, because a break harness that leaves the tree dirty measures the mutant
        on the next run and reports it green."""
        assert _run().passed


class TestTheRungInTheLadder:
    def test_m4_is_buildable_and_is_an_assembly(self) -> None:
        rung = mission("M4")

        assert rung.buildable
        assert rung.is_assembly
        assert rung.needs == ()

    def test_it_moved_by_its_own_rule(self) -> None:
        """E12 is complete and E13.2's open half is a document — ISO 286's deviation
        tables — not the stack-up arithmetic a gearbox's axial chain uses. A rung
        held pending on the half of a prerequisite it does not use is a ladder that
        has stopped measuring anything, which is M6's entry in this same file.

        **This asserts M4's own membership, not the whole buildable list.** It
        listed `["M1", "M2", "M3", "M4", "M6"]` verbatim until 2026-09-17, which
        made M5 and M7 landing afterwards read as *this* rung regressing — the
        same trap the blocked-case count in `app/verify/nafems.py` fell into
        twice. A test about M4 asserts M4, and the ladder's own total belongs to
        whichever rung last moved it.
        """
        from app.design.missions import LADDER

        buildable = [rung.rung for rung in LADDER if rung.buildable]
        assert "M4" in buildable
        # M4 stands on nothing M5, M6 or M7 build, so it must be buildable
        # whatever happened to them — and it must not have overtaken M1-M3.
        assert {"M1", "M2", "M3"} <= set(buildable)

    def test_it_does_not_claim_the_gears_have_teeth(self) -> None:
        """The rung's headline caveat, and the argument for a gear-profile operation
        on the open kernel: `catia_sketch_gear_profile` generates an involute on a
        CATIA seat, `app/kernel/occt/refusals.py` answers it 'not needed', and this
        is the case against that answer."""
        caveats = mission("M4").unproven

        assert any("there are no teeth" in caveat for caveat in caveats)
        assert any("catia_sketch_gear_profile" in caveat for caveat in caveats)

    def test_it_does_not_claim_a_bearing_life(self) -> None:
        assert any("no bearing life" in caveat for caveat in mission("M4").unproven)

    def test_it_does_not_claim_a_tooth_rating(self) -> None:
        """ISO 6336's bending and contact stresses are what decide whether these
        gears last. The module and face width here were chosen to make a machine."""
        assert any("ISO 6336" in caveat for caveat in mission("M4").unproven)

    def test_it_does_not_claim_lubrication_or_seals(self) -> None:
        """A gearbox is an oil bath with a level, a breather and two shaft seals,
        and its ladder column names lubrication as one of the four hard things."""
        assert any("lubrication" in caveat for caveat in mission("M4").unproven)

    def test_the_caveats_reach_the_public_gallery(self) -> None:
        """The gallery is derived from the ladder for exactly this reason — a page
        printing the passes and dropping the caveats would be the most misleading
        page in the product, because it would be the most convincing."""
        from app.handbook.gallery import entry_for

        entry = entry_for(mission("M4"))

        assert entry.buildable
        assert entry.not_claimed == tuple(mission("M4").unproven)
        assert entry.waiting_on == ()
