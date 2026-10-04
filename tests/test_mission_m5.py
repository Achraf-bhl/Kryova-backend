"""M5 — the stamping press. The rung the master plan calls the honest mid-point.

Its words: structure, mechanism, sheet metal, bought parts, fatigue and guarding
at once, and *if M5 does not work the phases before it were decoration*. So the
useful output of this rung is not that it builds. Eleven parts in a C-frame is a
morning's work. It is the ten-entry list of what a press needs that nothing in
this repository can say, and the two of those that this rung found by trying.

What makes it a rung rather than a stack of blocks is one idea: **a press is a
chain of dimensions from the crank down to the strip.** The slide's face at bottom
dead centre is `crank_axis - (rod + throw)`; the upper shoe hangs under the slide,
the lower shoe under that, the bolster under that, the bed under that. Not one of
those heights is typed. What falls out at the bottom is the gap between the two
die shoes — and that gap **is the strip being stamped**. Lengthen the connecting
rod by a millimetre and the dies close through the work; shorten it and the press
never cuts. It is measured between the built solids through an interface contract,
and `TestThePressBuiltWrong` lengthens the rod and watches it fail.

The second idea is the one E17.3 left open. `AssemblyDesign.parts` maps a
component to a `DesignSpec`, and a `SheetMetalPart` cannot compile to one — M3's
finding, still true, and here it blocks one level up, because it is what stops a
folded part being a *component*. So the guard is declared twice: as a fold tree,
which is what unfolds into the blank a laser cuts, and as the same radiused
section drawn by hand and extruded, which is what the kernel builds. The number
holding them together is the volume, and this file asks for it exactly.

Five parts, the split `app/design/` keeps everywhere.

* **The arithmetic, written out independently** — the slider-crank, the vertical
  chain, the blanking limit and every part's volume re-derived here from the
  press's dimensions, never read back from `missions.py`. A test that takes its
  expectation from the thing it checks agrees with any mistake that thing makes.
* **The guard as sheet metal** — the fold tree, its blank, and the identity
  relating the two.
* **The press built on OCCT**, required to agree with that arithmetic in six
  directions.
* **The press built wrong** — one break per guard, and a guard nobody has seen
  fail is a guard nobody knows works.
* **The rung in the ladder** — that it says what it does not claim, and that the
  caveats reach the public gallery rather than stopping here.

**These tests were written on Linux on 2026-09-16 and were not run as pytest**, at
the user's instruction. Every number below was measured first, by building the
real press through the real OCCT kernel with a one-off script: the mass, the
centre of mass, the envelope, the measured die gap, the guard's volume and every
break's failing claim are transcriptions of that run and not predictions of it.

Two things that run found, both recorded here as tests because both are the kind
of mistake that leaves a plausible number rather than an error:

1. **The crank pin was drawn as a block and weighed as a cylinder.** The roll-up
   and the closed form disagreed by 2.16 kg in 5,691 — 0.04%, far too small to
   notice by eye, and exactly what the mass claim exists to catch.
2. **A formability finding can never be an assertion on an assembly.** It was
   written as one, measuring a parameter, and came back NOT CHECKED: an
   `AssemblyDesign`'s parameters resolve the *bound* side of a claim and never the
   measured side, which is geometry the kernel reported. The kernel never sees a
   fold tree. So the check moved to construction, where it is stronger than a
   claim — the press cannot be built with a guard that will not fold.

The kernel is imported inside the tests that need it, as `test_mission_m2.py`
does, so collecting this file does not drag ~166 MB of OCP into every run.
"""

from __future__ import annotations

import math

import pytest

from app.design import missions as m
from app.design.errors import SpecError
from app.design.missions import Mission, MissionResult, mission, run_mission
from app.sheetmetal import blank_volume_difference_mm3, check_part, folded_volume_mm3, unfold

# --------------------------------------------------------------------------
# The press, written out here from its own drawing. Nothing below is imported
# from `missions.py`: these are the numbers a second engineer would arrive at.
# --------------------------------------------------------------------------

RATED_FORCE_N = 400_000.0
BLANK_LENGTH_MM = 120.0
BLANK_WIDTH_MM = 80.0
STRIP_MM = 2.0

THROW_MM = 50.0
ROD_MM = 400.0
CRANK_AXIS_Z_MM = 1_182.0
PIN_DIAMETER_MM = 80.0
PIN_LENGTH_MM = 200.0

SLIDE_MM = 300.0
UPPER_SHOE_MM = 50.0
LOWER_SHOE_MM = 50.0
BOLSTER_MM = 80.0
BED_MM = 250.0

FRAME_WIDTH_MM = 900.0
THROAT_DEPTH_MM = 600.0
COLUMN_DEPTH_MM = 250.0
CROWN_MM = 250.0
BOLSTER_WIDTH_MM = 800.0
BOLSTER_DEPTH_MM = 500.0
LOWER_SHOE_WIDTH_MM = 560.0
SHOE_DEPTH_MM = 300.0
UPPER_SHOE_WIDTH_MM = 400.0
SLIDE_WIDTH_MM = 400.0
SLIDE_DEPTH_MM = 500.0
ROD_SECTION_MM = 120.0
POST_DIAMETER_MM = 50.0
POST_MM = 320.0

GUARD_THICKNESS_MM = 2.0
GUARD_RADIUS_MM = 3.0
GUARD_WIDTH_MM = 400.0
GUARD_HEIGHT_MM = 300.0
GUARD_LENGTH_MM = 800.0

DENSITY_KG_M3 = 7870.0

#: The chain, derived downward from the crank axis exactly as the press is.
SLIDE_TOP_MM = CRANK_AXIS_Z_MM - (ROD_MM + THROW_MM)
SLIDE_BOTTOM_MM = SLIDE_TOP_MM - SLIDE_MM
UPPER_SHOE_BOTTOM_MM = SLIDE_BOTTOM_MM - UPPER_SHOE_MM
LOWER_SHOE_TOP_MM = UPPER_SHOE_BOTTOM_MM - STRIP_MM
LOWER_SHOE_BOTTOM_MM = LOWER_SHOE_TOP_MM - LOWER_SHOE_MM
BOLSTER_BOTTOM_MM = LOWER_SHOE_BOTTOM_MM - BOLSTER_MM
BED_BOTTOM_MM = BOLSTER_BOTTOM_MM - BED_MM
COLUMN_HEIGHT_MM = 1_500.0

#: Measured on the real kernel on 2026-09-16, not predicted.
MEASURED_MASS_KG = 5689.490996351077
MEASURED_COM_Y_MM = 198.41021380014197
MEASURED_COM_Z_MM = 710.3753041382826
MEASURED_GUARD_VOLUME_MM3 = 1588106.1929829656
MEASURED_GUARD_MASS_KG = 12.49839573877594
FOLD_GUARD_VOLUME_MM3 = 1588106.1929829747
GUARD_FLAT_LENGTH_MM = 991.7434166885081
GUARD_BLANK_DIFFERENCE_MM3 = 1316.7262813617235
GUARD_K = 0.3690228147639203


def cylinder_mm3(diameter_mm: float, length_mm: float) -> float:
    return math.pi / 4.0 * diameter_mm**2 * length_mm


def expected_volumes() -> dict[str, float]:
    """Every part's volume, from its own shape. The bill of materials, by hand."""
    return {
        "bed": FRAME_WIDTH_MM * THROAT_DEPTH_MM * BED_MM,
        "column": FRAME_WIDTH_MM * COLUMN_DEPTH_MM * COLUMN_HEIGHT_MM,
        "crown": FRAME_WIDTH_MM * THROAT_DEPTH_MM * CROWN_MM,
        "bolster": BOLSTER_WIDTH_MM * BOLSTER_DEPTH_MM * BOLSTER_MM,
        "lower_shoe": LOWER_SHOE_WIDTH_MM * SHOE_DEPTH_MM * LOWER_SHOE_MM,
        "upper_shoe": UPPER_SHOE_WIDTH_MM * SHOE_DEPTH_MM * UPPER_SHOE_MM,
        "guide_post": cylinder_mm3(POST_DIAMETER_MM, POST_MM),
        "slide": SLIDE_WIDTH_MM * SLIDE_DEPTH_MM * SLIDE_MM,
        "connecting_rod": ROD_SECTION_MM**2 * (CRANK_AXIS_Z_MM - THROW_MM
                                               - PIN_DIAMETER_MM / 2.0 - SLIDE_TOP_MM),
        "crank_pin": cylinder_mm3(PIN_DIAMETER_MM, PIN_LENGTH_MM),
    }


def expected_mass_kg() -> float:
    """Twelve occurrences of eleven parts. Two guide posts, one of everything else."""
    volumes = expected_volumes()
    total = sum(volumes.values()) + volumes["guide_post"] + FOLD_GUARD_VOLUME_MM3
    return total * 1e-9 * DENSITY_KG_M3


def _rung(**overrides: object) -> Mission:
    return Mission(
        rung="M5",
        title="Sheet-metal stamping press",
        era="V",
        hard="Force path, frame stiffness, die set, drive, guarding",
        assembly=m._m5_design(**overrides),  # type: ignore[arg-type]
        assertions=m._M5_ASSERTIONS,
        unproven=m._M5_UNPROVEN,
    )


def _run(rung: Mission | None = None) -> MissionResult:
    from app.kernel import OcctRunner

    return run_mission(rung or mission("M5"), runner_factory=OcctRunner)


def _failed_claims(result: MissionResult) -> set[str]:
    if result.checks is None:
        return set()
    return {check.name for check in result.checks.failed}


def _violated_claims(result: MissionResult) -> set[str]:
    if result.assembly is None:
        return set()
    return {violation.claim for violation in result.assembly.violations}


class TestTheDriveIsArithmetic:
    """A slider-crank, written out rather than approximated."""

    def test_the_stroke_is_twice_the_throw_and_the_rod_does_not_enter_it(self) -> None:
        """The extremes are `l + r` and `l - r`, so the rod cancels. A press bought
        for a 100 mm stroke and built with a longer rod has the same stroke in a
        different place, which is the confusion worth stating out of the way."""
        assert m.m5_stroke_mm() == pytest.approx(2.0 * THROW_MM)
        assert m.m5_stroke_mm(throw_mm=37.5) == pytest.approx(75.0)

        far = m.m5_slide_drop_mm(0.0)
        near = m.m5_slide_drop_mm(180.0)
        assert far - near == pytest.approx(m.m5_stroke_mm(), abs=1e-9)
        assert far - near == pytest.approx(
            m.m5_slide_drop_mm(0.0, rod_mm=900.0) - m.m5_slide_drop_mm(180.0, rod_mm=900.0)
        )

    def test_bottom_dead_centre_is_rod_plus_throw_below_the_axis(self) -> None:
        assert m.m5_slide_drop_mm(0.0) == pytest.approx(ROD_MM + THROW_MM)
        assert m.m5_slide_drop_mm(180.0) == pytest.approx(ROD_MM - THROW_MM)

    def test_the_motion_is_not_harmonic_and_that_is_the_point(self) -> None:
        """The second-order term is what makes a press's velocity asymmetric about
        mid-stroke, and it is why a die's shear is not the same going in as coming
        out. At 90 degrees a pure cosine would put the slide exactly half way; the
        real mechanism does not, and the difference is `l - sqrt(l^2 - r^2)`."""
        harmonic_half = ROD_MM
        real = m.m5_slide_drop_mm(90.0)

        assert real == pytest.approx(math.sqrt(ROD_MM**2 - THROW_MM**2))
        assert real < harmonic_half
        assert harmonic_half - real == pytest.approx(
            ROD_MM - math.sqrt(ROD_MM**2 - THROW_MM**2)
        )

    def test_it_is_symmetric_about_the_dead_centres(self) -> None:
        for angle in (17.0, 45.0, 90.0, 133.0):
            assert m.m5_slide_drop_mm(angle) == pytest.approx(
                m.m5_slide_drop_mm(-angle), abs=1e-9
            )

    def test_a_rod_shorter_than_the_throw_is_refused_in_words(self) -> None:
        """Not a domain error out of `sqrt`: a crank whose rod is shorter than its
        throw cannot turn, and the refusal says so rather than reporting a maths
        failure about a machine that could never be built."""
        with pytest.raises(SpecError) as excinfo:
            m.m5_slide_drop_mm(90.0, throw_mm=50.0, rod_mm=40.0)

        assert "cannot turn" in str(excinfo.value)
        assert "longer than the throw" in str(excinfo.value)

    def test_a_rod_exactly_as_long_as_the_throw_is_refused_too(self) -> None:
        """The boundary, which the strict inequality has to own: at `l == r` the
        square root is zero at 90 degrees and the mechanism is at a singularity."""
        with pytest.raises(SpecError):
            m.m5_slide_drop_mm(90.0, throw_mm=50.0, rod_mm=50.0)


class TestTheVerticalChainIsDerivedAndNotTyped:
    """Every height is a consequence of the one above it."""

    def test_the_slide_face_hangs_off_the_crank(self) -> None:
        assert m._M5_SLIDE_TOP_MM == pytest.approx(SLIDE_TOP_MM)
        assert m._M5_SLIDE_TOP_MM == pytest.approx(
            CRANK_AXIS_Z_MM - m.m5_slide_drop_mm(0.0)
        )

    def test_the_chain_reaches_the_floor_at_zero(self) -> None:
        """Not a coincidence worth asserting for its own sake — it is the check that
        the six heights between the crank and the floor add up to the frame that was
        drawn. A plate thickness changed in one place and not the other moves this."""
        assert m._M5_BED_BOTTOM_MM == pytest.approx(BED_BOTTOM_MM)
        assert m._M5_BED_BOTTOM_MM == pytest.approx(0.0)

    def test_the_die_gap_is_the_strip(self) -> None:
        """The whole rung in one subtraction."""
        gap = m._M5_UPPER_SHOE_BOTTOM_MM - m._M5_LOWER_SHOE_TOP_MM

        assert gap == pytest.approx(STRIP_MM)
        assert m._M5_LOWER_SHOE_TOP_MM == pytest.approx(LOWER_SHOE_TOP_MM)

    def test_a_longer_rod_closes_the_gap_one_for_one(self) -> None:
        """Arithmetic, before the kernel is asked. `TestThePressBuiltWrong` then
        builds it and watches the contract fail, which is the same claim measured
        between two solids instead of between two numbers."""
        structure = m._m5_structure(rod_mm=ROD_MM + 1.0)
        heights = {
            occurrence.component: occurrence.frame.origin_mm[2]
            for occurrence in structure.occurrences()
        }

        assert heights["upper_shoe"] == pytest.approx(UPPER_SHOE_BOTTOM_MM - 1.0)
        # The lower shoe does not move: it hangs off the bolster, which stands on the
        # floor. So the whole millimetre comes out of the gap the strip occupies.
        assert heights["lower_shoe"] == pytest.approx(LOWER_SHOE_BOTTOM_MM)

    def test_the_rod_body_stops_at_the_pin_surface(self) -> None:
        """The big end that wraps the pin is not modelled, because two solids sharing
        a journal interfere by construction and the clash check would be right to say
        so. The rod is therefore centre-to-centre `ROD_MM` and drawn shorter."""
        assert m._M5_ROD_BODY_MM == pytest.approx(
            CRANK_AXIS_Z_MM - THROW_MM - PIN_DIAMETER_MM / 2.0 - SLIDE_TOP_MM
        )
        assert m._M5_ROD_BODY_MM < ROD_MM


class TestTheTonnageClaimIsInverted:
    """The sharpest thing this rung found, and it is a refusal to state a number."""

    def test_the_blank_perimeter_is_the_cut_line(self) -> None:
        assert m.m5_blank_perimeter_mm() == pytest.approx(
            2.0 * (BLANK_LENGTH_MM + BLANK_WIDTH_MM)
        )
        assert m.m5_blank_perimeter_mm() == pytest.approx(400.0)

    def test_the_limit_is_the_rating_over_the_sheared_area(self) -> None:
        """`F = L t tau`, so a press of rated force F covers any material whose shear
        strength is at most `F / (L t)`. Exact, and needing no material property."""
        expected = RATED_FORCE_N / (m.m5_blank_perimeter_mm() * STRIP_MM)

        assert m.m5_shear_strength_limit_mpa() == pytest.approx(expected)
        assert m.m5_shear_strength_limit_mpa() == pytest.approx(500.0)

    def test_a_thicker_strip_lowers_the_limit_in_proportion(self) -> None:
        assert m.m5_shear_strength_limit_mpa(strip_mm=4.0) == pytest.approx(
            m.m5_shear_strength_limit_mpa(strip_mm=2.0) / 2.0
        )

    def test_the_repository_carries_no_shear_strength_to_check_it_against(self) -> None:
        """**The premise the inversion rests on, checked rather than assumed.**
        `app/solve/materials.py` transcribes yield and ultimate *tensile* strength
        for steel-1018 with a source and has no shear column, and every published
        ratio between the two is a shop rule of thumb this rung will not invent. If
        somebody adds a sourced shear strength, this test fails — and it should,
        because the caveat below it would then be out of date and the press could
        say something it currently cannot."""
        from app.solve.materials import MATERIAL_PROPERTIES, RECORDS

        steel = RECORDS["steel-1018"]
        assert "yield_strength_mpa" in steel.properties
        assert "ultimate_tensile_strength_mpa" in steel.properties
        assert steel.missing(("shear_strength_mpa",)) == ("shear_strength_mpa",)
        # Stronger than "steel-1018 has no shear strength": the property is not in
        # the vocabulary at all, so no material here could carry one. The only
        # `shear` the table knows is the elastic modulus, which is not a strength.
        assert [k for k in MATERIAL_PROPERTIES if "shear" in k] == ["shear_modulus_mpa"]
        # And even the two tensile figures are typical values rather than guaranteed
        # minima, which is a second reason this rung states a limit and not a verdict.
        assert steel.not_design_basis() == (
            "yield_strength_mpa",
            "ultimate_tensile_strength_mpa",
        )

    def test_the_caveat_names_the_gap_and_the_phase_that_owns_it(self) -> None:
        caveat = next(c for c in m._M5_UNPROVEN if "shear strength" in c)

        assert caveat.startswith("E12")
        assert "greatest shear strength its rating covers" in caveat

    def test_a_blank_with_no_perimeter_is_refused(self) -> None:
        with pytest.raises(SpecError):
            m.m5_shear_strength_limit_mpa(strip_mm=0.0)


class TestTheGuardIsSheetMetalAndSaysSo:
    """Declared twice, and the volume is the only thing holding the two together."""

    def test_it_is_a_chain_and_not_a_tree(self) -> None:
        """Rooted at a wall, for M3's reason: rooted at the roof it is a *tree* — two
        walls off one panel — and `unfold` refuses a flat length for a tree, because
        a branching blank has an extent in two directions and no chain to sum along.
        Rooted at a wall, `flat_length_mm` exists and is computed twice, once from the
        bend deductions and once from the allowances, and refused if the two differ."""
        pattern = unfold(m._m5_guard_part())

        assert pattern.flat_length_mm is not None
        assert pattern.flat_length_mm == pytest.approx(GUARD_FLAT_LENGTH_MM)

    def test_the_blank_is_shorter_than_the_metal_laid_end_to_end(self) -> None:
        """Two bends, so the blank is two bend deductions short of the girth. A flat
        length equal to the girth means the bend allowance did nothing."""
        girth = 2.0 * GUARD_HEIGHT_MM + GUARD_WIDTH_MM
        pattern = unfold(m._m5_guard_part())
        assert pattern.flat_length_mm is not None

        assert pattern.flat_length_mm < girth
        assert girth - pattern.flat_length_mm == pytest.approx(
            2.0 * (
                2.0 * math.tan(math.radians(45.0)) * (GUARD_RADIUS_MM + GUARD_THICKNESS_MM)
                - math.radians(90.0) * (GUARD_RADIUS_MM + GUARD_K * GUARD_THICKNESS_MM)
            ),
            abs=1e-9,
        )

    def test_the_k_factor_is_the_ladders_and_not_this_rungs(self) -> None:
        """One K doctrine for the whole ladder — DIN 6935, from `_m3_k_factor` — so a
        guard and an enclosure folded off the same coil do not get their neutral axes
        from different traditions."""
        k = m._m3_k_factor(
            inside_radius_mm=GUARD_RADIUS_MM,
            thickness_mm=GUARD_THICKNESS_MM,
            grade="steel_mild_cr",
        )

        assert k.value == pytest.approx(GUARD_K)
        assert "DIN 6935" in k.source.citation

    def test_the_folded_volume_exceeds_the_blank_by_exactly_what_k_costs(self) -> None:
        """An identity, not a tolerance: each bend contributes `theta t^2 w (0.5 - K)`,
        zero at K = 0.5 because a neutral axis on the mid-plane conserves material
        exactly. Both layouts consume one `unfold.tangent_extents` walk, which is what
        makes this evidence rather than two calculations that happen to agree."""
        expected = (
            2.0
            * math.radians(90.0)
            * GUARD_THICKNESS_MM**2
            * GUARD_LENGTH_MM
            * (0.5 - GUARD_K)
        )

        assert blank_volume_difference_mm3(m._m5_guard_part()) == pytest.approx(
            expected, rel=1e-9
        )
        assert blank_volume_difference_mm3(
            m._m5_guard_part()
        ) == pytest.approx(GUARD_BLANK_DIFFERENCE_MM3)

    def test_a_guard_that_cannot_be_folded_is_refused_at_construction(self) -> None:
        """**A refusal and not an assertion, and the reason is structural.** It was
        written as a claim first and came back NOT CHECKED: an `AssemblyDesign`'s
        parameters resolve the *bound* side of an assertion and never the measured
        side, which is geometry the kernel reported — and the kernel never sees a fold
        tree. An unchecked assertion is UNMEASURED, which is honest and useless. So
        the press cannot be built with a guard that will not fold."""
        with pytest.raises(SpecError) as excinfo:
            m._m5_guard_part(inside_radius_mm=0.2)

        message = str(excinfo.value)
        assert "cannot be folded" in message
        assert "minimum bend radius" in message
        assert "steel_mild_cr" in message

    def test_an_unassessed_guard_is_refused_as_well_as_a_failing_one(self) -> None:
        """`FormabilityReport` keeps `ok` and `complete` apart because a part with no
        failures and three unmeasured checks has not been assessed. A rung reading
        only `ok` would repeat here the exact mistake that class exists to prevent."""
        report = check_part(m._m5_guard_part())

        assert report.ok
        assert report.complete
        assert report.failed == ()
        assert report.unmeasured == ()

    def test_the_section_traces_one_closed_contour(self) -> None:
        """Twelve segments: six lines and four arcs round the outside and the inside,
        plus the two free edges. Every segment end is computed the same way at both
        ends of every join, so the chaining in `app.kernel.occt.sketching` sees exact
        matches rather than near ones."""
        features = m._m5_guard_section(
            thickness_mm=GUARD_THICKNESS_MM,
            inside_radius_mm=GUARD_RADIUS_MM,
            width_mm=GUARD_WIDTH_MM,
            height_mm=GUARD_HEIGHT_MM,
            sketch="guard.section",
        )
        tools = [feature.op for feature in features]

        assert len(features) == 12
        assert tools.count("catia_sketch_arc") == 4
        assert tools.count("catia_sketch_line") == 8

    def test_the_section_uses_the_real_bend_radii_inside_and_out(self) -> None:
        """Which is why the volumes are asked to agree *exactly* rather than within a
        tolerance: this is not an approximation of the folded part, it is its
        section. Two arcs at `r` and two at `r + t`."""
        features = m._m5_guard_section(
            thickness_mm=GUARD_THICKNESS_MM,
            inside_radius_mm=GUARD_RADIUS_MM,
            width_mm=GUARD_WIDTH_MM,
            height_mm=GUARD_HEIGHT_MM,
            sketch="guard.section",
        )
        radii = sorted(
            f.args["radius_mm"] for f in features if f.op == "catia_sketch_arc"
        )

        assert radii == pytest.approx(
            [GUARD_RADIUS_MM, GUARD_RADIUS_MM] + [GUARD_RADIUS_MM + GUARD_THICKNESS_MM] * 2
        )


class TestTheClosedForm:
    """The bill of materials as arithmetic, before anything is built."""

    def test_every_part_is_the_volume_of_its_own_shape(self) -> None:
        assert m._m5_volumes() == pytest.approx(
            {**expected_volumes(), "guard": FOLD_GUARD_VOLUME_MM3}
        )

    def test_the_crank_pin_is_round(self) -> None:
        """The regression this file exists for as much as any other. It was drawn by
        the block helper and weighed as a cylinder, and the two disagreed by 2.16 kg
        in 5,691 — 0.04%, which is far too small to see and exactly the size of error
        the mass claim catches. One helper per shape is the fix."""
        assert m._m5_volumes()["crank_pin"] == pytest.approx(
            cylinder_mm3(PIN_DIAMETER_MM, PIN_LENGTH_MM)
        )
        assert m._m5_volumes()["crank_pin"] < PIN_DIAMETER_MM**2 * PIN_LENGTH_MM

    def test_the_press_weighs_what_its_parts_weigh(self) -> None:
        assert m.m5_mass_kg() == pytest.approx(expected_mass_kg(), rel=1e-9)
        assert m.m5_mass_kg() == pytest.approx(MEASURED_MASS_KG, rel=1e-9)

    def test_there_are_twelve_occurrences_of_eleven_parts(self) -> None:
        assert len(m._M5_OCCURRENCES) == 12
        assert len(set(m._M5_OCCURRENCES)) == 11
        assert m._M5_OCCURRENCES.count("guide_post") == 2

    def test_the_guard_carries_its_share_of_the_frame(self) -> None:
        """A 2 mm shell against a 5.7 tonne press: 0.22% of it. Worth asserting
        because it is the one part whose volume comes from another package, so a
        change in `app/sheetmetal/fold.py` moves the press's mass."""
        assert MEASURED_GUARD_MASS_KG / MEASURED_MASS_KG < 0.003
        assert MEASURED_GUARD_MASS_KG == pytest.approx(
            FOLD_GUARD_VOLUME_MM3 * 1e-9 * DENSITY_KG_M3, rel=1e-9
        )


class TestThePressBuilds:
    """Built on the real kernel and required to agree with the arithmetic."""

    def test_it_passes(self) -> None:
        result = _run()

        assert result.passed, result.reason

    def test_the_roll_up_equals_the_closed_form(self) -> None:
        result = _run()
        assert result.assembly is not None

        assert result.assembly.payload["mass_kg"] == pytest.approx(
            expected_mass_kg(), rel=1e-9
        )

    def test_the_die_set_closes_on_the_strip(self) -> None:
        """The rung, measured between two built solids rather than computed from the
        placements that put them there."""
        result = _run()
        assert result.assembly is not None
        contracts = {c.interface.name: c for c in result.assembly.contracts}

        die_set = contracts["die set at bottom dead centre"]
        assert die_set.ok, die_set.report.summary()
        measured = {r.assertion.measure: r.measured for r in die_set.report.results}
        assert measured["minimum_clearance_mm"] == pytest.approx(STRIP_MM, abs=1e-3)
        assert measured["provider.bounding_box_mm.size[1]"] == pytest.approx(
            SHOE_DEPTH_MM, abs=1e-3
        )
        assert measured["consumer.bounding_box_mm.size[1]"] == pytest.approx(
            SHOE_DEPTH_MM, abs=1e-3
        )

    def test_the_force_path_is_continuous_at_the_bolster(self) -> None:
        """Both numbers, because the minimum alone cannot carry a fit-up claim: a
        bolster touching at one edge and 10 mm clear at the other has a minimum
        clearance of zero and is a cracked bed waiting to happen."""
        result = _run()
        assert result.assembly is not None
        contracts = {c.interface.name: c for c in result.assembly.contracts}

        force_path = contracts["bolster to bed"]
        assert force_path.ok, force_path.report.summary()
        measured = {r.assertion.measure: r.measured for r in force_path.report.results}
        assert measured["minimum_clearance_mm"] == pytest.approx(0.0, abs=1e-6)
        assert measured["widest_gap_mm"] == pytest.approx(0.0, abs=1e-6)

    def test_the_guard_the_kernel_built_is_the_guard_the_fold_tree_describes(self) -> None:
        """**The number that joins the two halves of E17.3, and it agrees to 9.1e-09
        cubic millimetres out of 1.59 million** — a relative difference of 6e-15,
        which is floating point and not modelling. Two descriptions of one part, with
        nothing in the code base connecting them, arrived at independently."""
        result = _run()
        assert result.assembly is not None

        measured = result.assembly.payload["guard"]["volume_mm3"]
        assert measured == pytest.approx(MEASURED_GUARD_VOLUME_MM3, rel=1e-12)
        assert measured == pytest.approx(
            folded_volume_mm3(m._m5_guard_part()), abs=1e-6
        )
        assert abs(measured - FOLD_GUARD_VOLUME_MM3) < 1e-6

    def test_the_guard_is_one_solid_and_not_three_walls(self) -> None:
        """A fold that fused is a guard; three flanges that did not are a part that
        weighs the right amount and falls apart in the press shop."""
        result = _run()
        assert result.assembly is not None

        assert result.assembly.payload["guard"]["solid_count"] == 1

    def test_nothing_occupies_the_same_space_as_anything_else(self) -> None:
        """Sixteen of the sixty-six pairs are close enough to be worth measuring and
        the other fifty are separated by their bounding boxes; none interferes. The
        parts that touch — bed and bolster, shoe and post, rod and slide — read as
        zero clearance and not as a clash, which is what touching is."""
        result = _run()
        assert result.assembly is not None
        clash = result.assembly.payload["clash"]

        assert clash["occurrence_count"] == 12
        assert clash["clash_count"] == 0
        assert clash["interferes"] is False
        assert clash["complete"] is True
        assert clash["unchecked_pair_count"] == 0
        assert clash["minimum_clearance_mm"] == pytest.approx(0.0, abs=1e-6)

    def test_the_press_is_the_size_it_was_drawn(self) -> None:
        result = _run()
        assert result.assembly is not None
        envelope = result.assembly.payload["envelope_mm"]

        assert envelope["size"][0] == pytest.approx(FRAME_WIDTH_MM, abs=1e-3)
        assert envelope["size"][2] == pytest.approx(COLUMN_HEIGHT_MM, abs=1e-3)
        assert envelope["size"][1] == pytest.approx(
            THROAT_DEPTH_MM / 2.0 + COLUMN_DEPTH_MM + THROAT_DEPTH_MM / 2.0, abs=1e-3
        )

    def test_the_press_stands_on_the_floor(self) -> None:
        result = _run()
        assert result.assembly is not None

        assert result.assembly.payload["envelope_mm"]["min"][2] == pytest.approx(
            0.0, abs=1e-6
        )

    def test_it_is_symmetric_across_and_back_heavy_along(self) -> None:
        """A gap-frame press is back-heavy — the column is the single heaviest part
        and all of it is behind the work — which is why one is bolted to the floor.
        The claim that knows which way round the C faces."""
        result = _run()
        assert result.assembly is not None
        com = result.assembly.payload["centre_of_mass_mm"]

        assert com[0] == pytest.approx(0.0, abs=1e-6)
        assert com[1] == pytest.approx(MEASURED_COM_Y_MM, rel=1e-9)
        assert com[1] > 0.0
        assert com[2] == pytest.approx(MEASURED_COM_Z_MM, rel=1e-9)

    def test_every_leaf_was_weighed(self) -> None:
        result = _run()
        assert result.assembly is not None
        payload = result.assembly.payload

        assert payload["component_count"] == 11
        assert payload["weighed_occurrence_count"] == 12
        assert payload["unmeasured_occurrence_count"] == 0


class TestThePressBuiltWrong:
    """One break per guard. A mission that cannot fail proves nothing.

    Every failing claim named below was produced by running the break through the
    real kernel on 2026-09-16, not predicted from reading the assertion.
    """

    def test_a_connecting_rod_one_millimetre_long_crashes_the_dies(self) -> None:
        """The break the rung is for. The rod is not measured by any claim — what is
        measured is the gap at the bottom of the stroke, five parts further down the
        chain, and it closes from 2 mm to 1 mm."""
        result = _run(_rung(rod_mm=ROD_MM + 1.0))

        assert not result.passed
        assert "the shoes close on the strip and not on each other" in _violated_claims(
            result
        )

    def test_a_connecting_rod_one_millimetre_short_never_cuts(self) -> None:
        """The other direction, which is the failure a press shop would not notice
        for a shift: the dies simply do not close on the work."""
        result = _run(_rung(rod_mm=ROD_MM - 1.0))

        assert not result.passed
        assert "the shoes close on the strip and not on each other" in _violated_claims(
            result
        )

    def test_a_guard_dropped_onto_the_drive_is_a_clash(self) -> None:
        """The one mistake on this machine that injures somebody rather than
        scrapping a part."""
        result = _run(
            _rung(structure=m._m5_structure(guard_top_z_mm=1_100.0))
        )

        assert not result.passed
        assert _failed_claims(result) == {
            "nothing in the press occupies the same space as anything else"
        }

    def test_a_guide_post_nobody_counted_fails_three_claims_at_once(self) -> None:
        """A part in the graph that is in no bill of materials moves the mass, the
        count and the symmetry together — which is why none of the three is
        sufficient on its own and why the count is not the interesting one."""
        result = _run(_rung(structure=m._m5_structure(spare_post_at_mm=330.0)))

        assert not result.passed
        assert _failed_claims(result) == {
            "the roll-up equals the closed form over every occurrence",
            "the graph holds exactly the parts the bill of materials counts",
            "the press is symmetric about its own centre plane",
        }

    def test_one_guide_post_moved_fails_only_the_symmetry_claim(self) -> None:
        """**Measured rather than assumed**, and the reason the two posts are given
        as two positions rather than as one half-width: moving one of them leaves the
        mass, the count, the envelope and both contracts untouched, so this claim is
        the only thing standing between a die set that guides squarely and one that
        cocks the slide."""
        result = _run(_rung(structure=m._m5_structure(post_x_mm=(-250.0, 240.0))))

        assert not result.passed
        assert _failed_claims(result) == {
            "the press is symmetric about its own centre plane"
        }

    def test_the_press_built_right_fails_nothing(self) -> None:
        """The control. Without it, a break test passes for a suite that is red
        everywhere, which is the mutation-run failure `CLAUDE.md` records."""
        result = _run()

        assert _failed_claims(result) == set()
        assert _violated_claims(result) == set()


class TestTheRungInTheLadder:
    """That it builds, and that what it cannot say travels with it."""

    def test_m5_is_buildable_and_no_longer_pending(self) -> None:
        rung = mission("M5")

        assert rung.buildable
        assert rung.needs == ()
        assert rung.assembly is not None

    def test_it_moved_by_the_rule_and_not_by_a_decision(self) -> None:
        """M6's rule: a rung whose stated prerequisites are met and which is still
        marked pending is a ladder that has stopped measuring anything. E13.2 is the
        one open task among M5's declared needs and what is open in it is a document
        — ISO 286's deviation tables — which this rung does not read."""
        from app.design.missions import LADDER

        pending = [rung.rung for rung in LADDER if not rung.buildable]

        assert "M5" not in pending
        assert pending == ["M7", "M8", "M9"]

    def test_it_carries_what_it_does_not_claim(self) -> None:
        rung = mission("M5")

        assert len(rung.unproven) == 10
        assert all(caveat.startswith("E") for caveat in rung.unproven)

    def test_the_open_kernel_caveat_is_first_and_names_the_queue(self) -> None:
        """M3's finding, one level up: there is no sheet-metal operation in the CATIA
        registry and deliberately is not one, so the guard is an open-kernel claim.
        A mission implying a seat could build this part would be claiming a capability
        the code does not have, which is the thing `CLAUDE.md` forbids twice."""
        rung = mission("M5")

        assert rung.unproven[0].startswith("E1 —")
        assert "THE QUEUE E1" in rung.unproven[0]
        assert "open-kernel claim" in rung.unproven[0]

    def test_frame_stiffness_is_named_as_missing(self) -> None:
        """The thing a gap-frame press is actually bought or rejected on. A C opens
        under load: the throat deflects, the slide tips, and the die's clearance goes
        uneven down one side. 'The force path is continuous' is a geometry claim and
        this rung does not let it be read as a stiffness one."""
        caveat = next(c for c in mission("M5").unproven if "stiffness" in c)

        assert caveat.startswith("E6")
        assert "no load case has been run" in caveat.lower()

    def test_the_caveats_reach_the_public_gallery(self) -> None:
        """A gallery that printed the passes and dropped `Mission.unproven` would be
        the most misleading page in the product, because it would be the most
        convincing one (P10.2). `not_claimed` is a required field, and this is the
        test that it is filled from the rung rather than by hand."""
        from app.handbook.gallery import entry_for

        entry = entry_for(mission("M5"))

        assert entry.not_claimed == mission("M5").unproven
        assert entry.rung == "M5"

    def test_the_ladder_now_stands_at_six_of_nine(self) -> None:
        from app.design.missions import run_ladder
        from app.kernel import OcctRunner

        report = run_ladder(OcctRunner)

        assert len(report.pending) == 3
        assert "6/9 rungs pass" in report.summary()
        assert report.ok, report.summary()
        assert not report.complete
