"""M3 — the sheet-metal enclosure. The ladder's first rung that is *folded*.

Master plan 5.4 and Phase 17.3. M1 proved a spec compiles and builds; M2 proved
several parts fit together; M3 is the first rung where the part exists twice — as the
solid the kernel builds and as the blank the press brake cuts — and the interesting
question is whether those two are descriptions of the same object.

Four halves, and the split is the one `app/design/` keeps everywhere.

* **Declaration** — what a folded rung is, and the ways declaring one can be
  dishonest. Pure, offline, milliseconds.
* **The arithmetic, checked twice** — every closed form `missions.py` carries,
  recomputed here from the drawing, and the drawn cross-section integrated by Green's
  theorem to see whether it encloses what the piecewise formula says it does. No
  kernel: the section is a list of coordinates in the spec.
* **The cover itself** — built on OCCT, every number checked against the arithmetic
  above rather than against what the kernel said last time.
* **The cover built wrong** — nine ways, one per guard. A mission that cannot fail
  proves nothing, so every claim M3 makes is broken here on purpose at least once,
  including the one that ties the blank to the solid.

The kernel is imported inside the tests that need it, the way `test_mission_m2.py`
does, so collecting this file does not drag ~166 MB of OCP into every run.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.design import missions as m
from app.design.assertions import Outcome
from app.design.errors import SpecError
from app.design.missions import (
    FoldedDesign,
    Mission,
    MissionOutcome,
    MissionResult,
    StockSheet,
    mission,
    run_mission,
)
from app.design.spec import DesignSpec
from app.sheetmetal import (
    Basis,
    Bend,
    BendDirection,
    Flange,
    LengthConvention,
    SheetMetalPart,
    assumed,
    din6935,
    machinerys_handbook,
    sheet_material,
)
from app.solve.materials import Status

# --------------------------------------------------------------------------
# The arithmetic, written out independently.
#
# Every number below is computed here from the drawing, never read from
# `app.design.missions`. A test that derives its expectation from the thing it is
# checking agrees with any mistake that thing makes — and a cover folded to the wrong
# blank and one folded to the right blank are the same picture.
# --------------------------------------------------------------------------

T_MM = 1.5  # sheet thickness
R_MM = 2.0  # inside bend radius
WIDTH_MM = 200.0  # outside, across the cover
HEIGHT_MM = 60.0  # outside, roof to the underside of the lips
LIP_MM = 20.0  # return lip, to the outside mould line
DEPTH_MM = 150.0  # along the bend lines
HOLE_MM = 10.0
HOLE_U_MM = (60.0, 140.0)
DENSITY_KG_M3 = 7870.0  # steel-1018

#: DIN 6935's unfolding factor at r/t = 4/3, halved because DIN writes its
#: compensation value around `r + k*t/2` where ANSI writes it around `r + K*t`.
K = (0.65 + 0.5 * math.log10(R_MM / T_MM)) / 2.0

#: `BA = theta * (r + K*t)`, `SB = tan(theta/2)*(r+t)`, `BD = 2*SB - BA`. Ninety
#: degrees, so `tan(theta/2)` is one and the setback is `r + t` exactly.
BEND_ALLOWANCE_MM = math.pi / 2.0 * (R_MM + K * T_MM)
SETBACK_MM = R_MM + T_MM
BEND_DEDUCTION_MM = 2.0 * SETBACK_MM - BEND_ALLOWANCE_MM

FLAT_LENGTH_MM = 2.0 * LIP_MM + 2.0 * HEIGHT_MM + WIDTH_MM - 4.0 * BEND_DEDUCTION_MM
BLANK_AREA_MM2 = FLAT_LENGTH_MM * DEPTH_MM

#: Five straight runs of sheet and four quarter annuli.
SECTION_AREA_MM2 = T_MM * (
    (WIDTH_MM - 2.0 * SETBACK_MM)
    + 2.0 * (HEIGHT_MM - 2.0 * SETBACK_MM)
    + 2.0 * (LIP_MM - SETBACK_MM)
) + 4.0 * math.pi / 4.0 * ((R_MM + T_MM) ** 2 - R_MM**2)

HOLE_VOLUME_MM3 = len(HOLE_U_MM) * math.pi * (HOLE_MM / 2.0) ** 2 * T_MM
VOLUME_MM3 = SECTION_AREA_MM2 * DEPTH_MM - HOLE_VOLUME_MM3
MASS_KG = VOLUME_MM3 * 1e-9 * DENSITY_KG_M3

#: What every bend adds by keeping its thickness where the blank kept its neutral-axis
#: length: `theta * t^2 * (0.5 - K)` per mm of bend.
FOLD_GAIN_MM3 = 4.0 * (math.pi / 2.0) * T_MM**2 * (0.5 - K) * DEPTH_MM

#: Both surfaces of every straight run, both arcs of every bend, two lip edges, two
#: ends of the channel, and each hole swapping two discs for a bore.
SECTION_PERIMETER_MM = (
    2.0
    * (
        (WIDTH_MM - 2.0 * SETBACK_MM)
        + 2.0 * (HEIGHT_MM - 2.0 * SETBACK_MM)
        + 2.0 * (LIP_MM - SETBACK_MM)
    )
    + 2.0 * T_MM
    + 4.0 * (math.pi / 2.0) * (2.0 * R_MM + T_MM)
)
SURFACE_AREA_MM2 = (
    SECTION_PERIMETER_MM * DEPTH_MM
    + 2.0 * SECTION_AREA_MM2
    + len(HOLE_U_MM) * (math.pi * HOLE_MM * T_MM - 2.0 * math.pi * (HOLE_MM / 2.0) ** 2)
)

#: 2000 x 1000 sheet, bend lines across its length: five blanks along, six across.
PARTS_PER_SHEET = 5 * 6


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _variant(**overrides: Any) -> Mission:
    """M3, with the cover built to a different drawing. The break harness.

    Carries M3's own assertions, so what is being tested is whether the *shipped*
    claims catch the fault — not whether a claim written for the occasion does.
    """
    return Mission(
        rung="M3",
        title="Sheet-metal enclosure",
        era="IV",
        hard="Unfolding, bend allowance, DFM",
        folded=m._m3_design(**overrides),
        assertions=m._M3_ASSERTIONS,
        unproven=m._M3_UNPROVEN,
    )


def _run(rung: Mission | None = None) -> MissionResult:
    from app.kernel import OcctRunner

    return run_mission(rung or mission("M3"), OcctRunner())


def _failed_claims(result: MissionResult) -> set[str]:
    """Names of the mission's own assertions that came back FAILED."""
    if result.checks is None:
        return set()
    return {r.name for r in result.checks.failed}


def _unmeasured_claims(result: MissionResult) -> set[str]:
    if result.checks is None:
        return set()
    return {r.name for r in result.checks.unmeasured}


def _refused_checks(result: MissionResult) -> set[str]:
    """The formability checks that came back FAILED, by the check's own name."""
    if result.folded is None or result.folded.formability is None:
        return set()
    return {finding.check for finding in result.folded.formability.failed}


def _section_boundary(spec: DesignSpec, *, per_arc: int = 2000) -> list[tuple[float, float]]:
    """The drawn cross-section as a dense closed polygon, read off the spec itself.

    Reads the *design*, not a helper written beside it: the coordinates in the
    `catia_sketch_line` and `catia_sketch_arc` arguments are what the kernel is handed,
    so integrating them answers "does the thing that gets built enclose the area the
    closed form claims" rather than "do two formulae agree".
    """
    points: list[tuple[float, float]] = []
    for feature in spec.features:
        arguments = feature.args
        if feature.op == "catia_sketch_line":
            start = tuple(float(v) for v in arguments["start"])
            end = tuple(float(v) for v in arguments["end"])
            points.append((start[0], start[1]))
            points.append((end[0], end[1]))
        elif feature.op == "catia_sketch_arc":
            centre = [float(v) for v in arguments["centre"]]
            radius = float(arguments["radius_mm"])
            first = float(arguments["start_angle_deg"])
            last = float(arguments["end_angle_deg"])
            for step in range(per_arc + 1):
                angle = math.radians(first + (last - first) * step / per_arc)
                points.append(
                    (
                        centre[0] + radius * math.cos(angle),
                        centre[1] + radius * math.sin(angle),
                    )
                )
    return points


def _polygon_area_and_centroid(
    points: Sequence[tuple[float, float]],
) -> tuple[float, tuple[float, float]]:
    """Green's theorem over a closed polygon. Sign-independent: the area is absolute."""
    twice_area = 0.0
    cx = 0.0
    cy = 0.0
    for (x0, y0), (x1, y1) in zip(points, [*points[1:], points[0]], strict=True):
        cross = x0 * y1 - x1 * y0
        twice_area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    area = twice_area / 2.0
    return abs(area), (cx / (6.0 * area), cy / (6.0 * area))


# --------------------------------------------------------------------------
# Declaration
# --------------------------------------------------------------------------


class TestAFoldedRungIsDeclaredHonestly:
    """The four ways of declaring one that would report on something else."""

    def test_a_folded_part_with_no_bends_is_refused(self) -> None:
        """Everything this rung checks is a property of a bend."""
        flat = SheetMetalPart(
            name="not folded at all",
            material=sheet_material("steel_mild_cr", thickness_mm=T_MM),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=Flange(name="plate", length_mm=100.0, width_mm=50.0),
        )
        with pytest.raises(SpecError, match="no bends"):
            FoldedDesign(spec=m._m3_spec(), part=flat)

    def test_a_rung_cannot_be_a_part_and_a_folded_sheet_at_once(self) -> None:
        """Two designs, one set of claims, and no way to say which was checked."""
        with pytest.raises(SpecError, match="a part design and a folded sheet"):
            Mission(
                rung="M3",
                title="t",
                era="IV",
                hard="h",
                spec=m._m1_spec(),
                folded=m._m3_design(),
                assertions=m._M3_ASSERTIONS,
            )

    def test_a_rung_cannot_be_an_assembly_and_a_folded_sheet_at_once(self) -> None:
        with pytest.raises(SpecError, match="an assembly and a folded sheet"):
            Mission(
                rung="M3",
                title="t",
                era="IV",
                hard="h",
                assembly=m._m2_design(),
                folded=m._m3_design(),
                assertions=m._M3_ASSERTIONS,
            )

    def test_a_folded_rung_cannot_also_be_waiting(self) -> None:
        """A rung is buildable or it is pending; both would let it report as either."""
        with pytest.raises(SpecError, match="both a design and `needs`"):
            Mission(
                rung="M3",
                title="t",
                era="IV",
                hard="h",
                folded=m._m3_design(),
                assertions=m._M3_ASSERTIONS,
                needs=("E17.3 — sheet metal",),
            )

    def test_a_folded_rung_that_claims_nothing_is_refused(self) -> None:
        with pytest.raises(SpecError, match="claims nothing"):
            Mission(
                rung="M3", title="t", era="IV", hard="h", folded=m._m3_design()
            )

    def test_the_ladder_now_declares_m3_as_folded(self) -> None:
        rung = mission("M3")

        assert rung.buildable
        assert rung.is_folded
        assert not rung.is_assembly
        assert rung.folded is not None
        assert rung.unproven, "a rung that builds still says what it does not claim"

    def test_a_stock_sheet_needs_two_real_dimensions(self) -> None:
        with pytest.raises(SpecError, match="not a sheet"):
            StockSheet(name="nothing", length_mm=0.0, width_mm=1000.0)


# --------------------------------------------------------------------------
# The arithmetic
# --------------------------------------------------------------------------


class TestTheClosedFormsAreWhatTheyClaim:
    """Every number M3 asserts against, recomputed here from the drawing."""

    def test_the_k_factor_is_din_6935_on_steel(self) -> None:
        k = m._M3_K_FACTOR

        assert k.value == pytest.approx(K, rel=1e-12)
        assert k.basis is Basis.STANDARD_FORMULA
        assert k.status is Status.SPECIFIED, (
            "the strongest basis in the package short of a test bend, and the reason "
            "DIN was chosen over Machinery's Handbook here"
        )
        assert k.has_stated_basis

    def test_the_two_traditions_really_do_disagree(self) -> None:
        """The caveat is a measured number, not a scruple."""
        family = sheet_material("steel_mild_cr", thickness_mm=T_MM).family
        ansi = machinerys_handbook(
            inside_radius_mm=R_MM, thickness_mm=T_MM, family=family
        )
        din = din6935(inside_radius_mm=R_MM, thickness_mm=T_MM, family=family)

        spread = 4.0 * (math.pi / 2.0) * T_MM * abs(ansi.value - din.value)

        assert ansi.value != pytest.approx(din.value, rel=1e-3)
        assert m._M3_K_SPREAD_MM == pytest.approx(spread, rel=1e-12)
        assert spread > 0.5, "0.85 mm of blank over four bends is not rounding"
        assert f"{spread:.2f}" in " ".join(m._M3_UNPROVEN)

    def test_the_bend_arithmetic(self) -> None:
        assert m._M3_BEND_ALLOWANCE_MM == pytest.approx(BEND_ALLOWANCE_MM, rel=1e-12)
        assert m._M3_SETBACK_MM == pytest.approx(SETBACK_MM, rel=1e-12)
        assert m._M3_BEND_DEDUCTION_MM == pytest.approx(BEND_DEDUCTION_MM, rel=1e-12)

    def test_the_blank(self) -> None:
        assert m._M3_FLAT_LENGTH_MM == pytest.approx(FLAT_LENGTH_MM, rel=1e-12)
        assert m._M3_BLANK_AREA_MM2 == pytest.approx(BLANK_AREA_MM2, rel=1e-12)

    def test_the_solid(self) -> None:
        assert m._M3_SECTION_AREA_MM2 == pytest.approx(SECTION_AREA_MM2, rel=1e-12)
        assert m._M3_VOLUME_MM3 == pytest.approx(VOLUME_MM3, rel=1e-12)
        assert m._M3_MASS_KG == pytest.approx(MASS_KG, rel=1e-12)
        assert m._M3_AREA_MM2 == pytest.approx(SURFACE_AREA_MM2, rel=1e-12)
        assert m._M3_FACES == 24

    def test_the_fold_gain_is_not_zero_and_is_where_the_two_models_part(self) -> None:
        """A flat pattern conserves neutral-axis length; a solid conserves thickness.

        The difference is `theta*t^2*(0.5-K)` per mm of bend and it vanishes only at
        `K = 0.5`. A mission that expected zero here would fail on a correct part.
        """
        assert m._M3_FOLD_GAIN_MM3 == pytest.approx(FOLD_GAIN_MM3, rel=1e-12)
        assert FOLD_GAIN_MM3 > 1.0
        assert 4.0 * (math.pi / 2.0) * T_MM**2 * (0.5 - 0.5) * DEPTH_MM == 0.0

    def test_the_reconciliation_does_not_depend_on_k(self) -> None:
        """Which is why the residual tests the geometry and never the judgement.

        `blank + gain` is `D*t*sum(tangents) + 4*D*theta*t*(r + t/2)` — the K in the
        bend allowance and the K in the fold gain cancel exactly. So an argued-about
        K moves the blank and cannot move the claim that the blank makes this part.
        """

        def reconciled(k: float) -> float:
            allowance = math.pi / 2.0 * (R_MM + k * T_MM)
            deduction = 2.0 * SETBACK_MM - allowance
            flat = 2.0 * LIP_MM + 2.0 * HEIGHT_MM + WIDTH_MM - 4.0 * deduction
            gain = 4.0 * (math.pi / 2.0) * T_MM**2 * (0.5 - k) * DEPTH_MM
            return flat * DEPTH_MM * T_MM + gain - HOLE_VOLUME_MM3

        assert reconciled(K) == pytest.approx(VOLUME_MM3, rel=1e-12)
        assert reconciled(0.5) == pytest.approx(reconciled(0.25), rel=1e-12)

    def test_the_drawn_section_encloses_what_the_closed_form_says(self) -> None:
        """The spec's own coordinates, integrated. No kernel and no shared formula."""
        area, (_, centre_y) = _polygon_area_and_centroid(_section_boundary(m._m3_spec()))

        assert area == pytest.approx(SECTION_AREA_MM2, rel=1e-7)

        # The mission's centre-of-mass claim is three dimensional and the holes come
        # out of the roof, so the section's own centroid is lifted to the solid's here.
        expected = (
            area * DEPTH_MM * centre_y - HOLE_VOLUME_MM3 * (-T_MM / 2.0)
        ) / (area * DEPTH_MM - HOLE_VOLUME_MM3)
        assert m._M3_CENTRE_Y_MM == pytest.approx(expected, abs=1e-5)

    def test_the_centroid_is_not_the_mid_line_guess(self) -> None:
        """A quarter annulus's centroid is not at `(R+r)/2`, and it matters here."""
        inner, outer = R_MM, R_MM + T_MM
        exact = (
            (2.0 / 3.0)
            * (outer**3 - inner**3)
            / (outer**2 - inner**2)
            * math.sin(math.pi / 4.0)
            / (math.pi / 4.0)
        )
        naive = (outer + inner) / 2.0 * math.sin(math.pi / 4.0) / (math.pi / 4.0)

        assert abs(exact - naive) > 5e-2, (
            "if these were the same the tolerance on the centre-of-mass claim would "
            "not be discriminating between them"
        )

    def test_the_nest_is_one_orientation_and_the_grain_says_which(self) -> None:
        blank_length, blank_width = FLAT_LENGTH_MM, DEPTH_MM
        along = int(2000.0 // blank_length) * int(1000.0 // blank_width)
        turned = int(2000.0 // blank_width) * int(1000.0 // blank_length)

        assert along == PARTS_PER_SHEET
        assert turned == 26
        assert along != turned, (
            "the orientations differ, so 'take the better one' is a real temptation — "
            "and turning the blank puts every bend along the rolling direction"
        )


# --------------------------------------------------------------------------
# The cover itself
# --------------------------------------------------------------------------


class TestTheCoverFolds:
    """M3 as shipped, built on OCCT and measured."""

    def test_the_rung_passes(self) -> None:
        result = _run()

        assert result.outcome is MissionOutcome.PASSED, result.reason
        assert result.checks is not None
        assert not result.checks.failed
        assert not result.checks.unmeasured

    def test_the_pass_prints_what_it_does_not_claim(self) -> None:
        """"M3 passes" must never come to mean "the blank is right"."""
        result = _run()

        printed = str(result)
        assert "not claimed" in printed
        assert "springback" in printed
        assert "test bend" in printed

    def test_it_is_still_a_chain_so_the_flat_length_is_cross_checked(self) -> None:
        """`unfold` computes it from the deductions and from the allowances, and
        refuses to report either if they disagree. Only a chain gets that."""
        result = _run()

        assert result.folded is not None
        assert result.folded.pattern is not None
        assert mission("M3").folded is not None
        assert m._m3_part().is_chain
        assert result.folded.pattern.flat_length_mm == pytest.approx(
            FLAT_LENGTH_MM, rel=1e-12
        )

    def test_the_solid_is_the_closed_form(self) -> None:
        payload = _payload_of(_run())

        assert payload["volume_mm3"] == pytest.approx(VOLUME_MM3, rel=1e-9)
        assert payload["mass_kg"] == pytest.approx(MASS_KG, rel=1e-9)
        assert payload["surface_area_mm2"] == pytest.approx(SURFACE_AREA_MM2, rel=1e-9)
        assert payload["face_count"] == 24
        assert payload["solid_count"] == 1

    def test_the_blank_accounts_for_the_solid(self) -> None:
        """The one number this rung exists for."""
        flat = _payload_of(_run())["flat"]

        assert flat["blank_volume_mm3"] == pytest.approx(
            BLANK_AREA_MM2 * T_MM, rel=1e-12
        )
        assert flat["fold_volume_gain_mm3"] == pytest.approx(FOLD_GAIN_MM3, rel=1e-9)
        assert flat["hole_volume_mm3"] == pytest.approx(HOLE_VOLUME_MM3, rel=1e-12)
        assert flat["volume_mismatch_mm3"] < 1e-6

    def test_every_forming_check_ran_and_passed(self) -> None:
        result = _run()

        assert result.folded is not None
        report = result.folded.formability
        assert report is not None
        assert report.ok
        assert report.complete, "an unmeasured check is not a pass"
        assert {finding.check for finding in report.findings} == {
            "K-factor basis",
            "minimum bend radius",
            "minimum flange length",
            "hole distance to bend",
        }

    def test_the_margins_are_published_not_just_the_verdicts(self) -> None:
        """"Nothing failed" does not say whether the tightest bend has room."""
        formability = _payload_of(_run())["formability"]

        assert formability["minimum_bend_radius_margin_mm"] == pytest.approx(
            R_MM - 1.0 * T_MM, rel=1e-9
        ), "R2 against a 1t minimum on 1.5 mm sheet: half a millimetre in hand"
        assert formability["worst_margin_mm"] > 0.0

    def test_the_blank_comes_off_the_sheet(self) -> None:
        result = _run()

        assert result.folded is not None
        nest = result.folded.nest
        assert nest is not None
        assert nest.parts_per_sheet == PARTS_PER_SHEET
        assert nest.across == 5
        assert nest.down == 6
        assert 0.75 < nest.utilisation < 0.80

    def test_nothing_in_the_report_is_approximate(self) -> None:
        """Every K names a source, so nothing downstream of one is an estimate."""
        result = _run()

        assert result.checks is not None
        assert not result.checks.approximate
        assert result.folded is not None
        assert result.folded.pattern is not None
        assert not result.folded.pattern.provisional

    def test_it_builds_the_same_cover_twice(self) -> None:
        """Determinism (1.6) at the level a mission cares about."""
        first, second = _run(), _run()

        assert first.build is not None and second.build is not None
        assert first.build.plan_digest == second.build.plan_digest
        assert _payload_of(first)["mass_kg"] == pytest.approx(
            _payload_of(second)["mass_kg"], rel=1e-12
        )
        assert _payload_of(first)["flat"]["volume_mismatch_mm3"] == pytest.approx(
            _payload_of(second)["flat"]["volume_mismatch_mm3"], abs=1e-12
        )

    def test_the_report_serialises(self) -> None:
        data = _run().to_dict()

        assert data["outcome"] == "passed"
        assert data["folded"]["flat_pattern"]["provisional"] is False
        assert data["folded"]["nest"]["parts_per_sheet"] == PARTS_PER_SHEET
        assert data["folded"]["formability"]["complete"] is True
        assert data["unproven"]


def _payload_of(result: MissionResult) -> Mapping[str, Any]:
    assert result.folded is not None
    return result.folded.payload


# --------------------------------------------------------------------------
# The cover built wrong
# --------------------------------------------------------------------------


class TestTheCoverBuiltWrong:
    """One break per guard. A guard nobody has seen fail is a guard nobody verified."""

    def test_a_lip_shorter_than_its_own_setback_does_not_flatten(self) -> None:
        """The bends at its ends eat the whole flange; there is no flat portion left."""
        result = _run(_variant(lip_mm=3.0))

        assert result.outcome is MissionOutcome.FAILED
        assert "does not flatten" in result.reason
        assert "no flat portion left" in result.reason
        assert result.folded is not None
        assert result.folded.pattern is None
        assert result.folded.build is None, (
            "a blank that cannot exist stops the rung before any geometry, because "
            "every number after it would describe a different object"
        )

    def test_a_radius_below_the_grade_minimum_is_refused(self) -> None:
        """Declared in 6061-T6, whose minimum inside radius is 3t — R2 cracks it.

        The blank does not move: DIN 6935's factor is a function of `r/t` alone, so
        the same radius in another family gives the same K and the same flat length.
        Exactly one check fires, which is what makes this a test of that check.
        """
        result = _run(_variant(grade="aluminium_6061_t6"))

        assert result.outcome is MissionOutcome.FAILED
        assert "minimum bend radius" in _refused_checks(result)
        assert _failed_claims(result) == {
            "nothing about forming the cover was refused",
            "the bends are inside the grade's minimum radius",
        }

    def test_swapping_the_grade_does_not_move_the_mass_and_that_is_the_gap(self) -> None:
        """`app.sheetmetal` and `app.solve.materials` are disjoint vocabularies.

        The cover above is declared in aluminium for the bend checks and still weighs
        what mild steel weighs, because the density comes from the design spec's own
        material slug and nothing relates the two catalogues. Pinned here so that the
        day a bridge lands, this test is the one that says the gap has closed.
        """
        result = _run(_variant(grade="aluminium_6061_t6"))
        payload = _payload_of(result)

        assert payload["flat"]["grade"] == "aluminium_6061_t6"
        assert payload["mass_kg"] == pytest.approx(MASS_KG, rel=1e-9)
        assert payload["density_kg_m3"] == pytest.approx(DENSITY_KG_M3)

    def test_a_hole_against_a_bend_tangent_is_refused(self) -> None:
        """Material within 2t of a tangent line stretches; the hole comes out oval."""
        result = _run(_variant(hole_u_mm=(6.0, 140.0)))

        assert result.outcome is MissionOutcome.FAILED
        assert "hole distance to bend" in _refused_checks(result)
        assert "the holes keep clear of the bend zones" in _failed_claims(result)

    def test_a_flange_that_misses_the_die_shoulder_is_refused(self) -> None:
        """Formed on a 60 mm V, the 20 mm lip is not held by anything."""
        result = _run(_variant(die_opening_mm=60.0))

        assert result.outcome is MissionOutcome.FAILED
        assert "minimum flange length" in _refused_checks(result)
        assert "every flange reaches the die shoulder" in _failed_claims(result)

    def test_a_k_factor_with_no_basis_fails_a_part_whose_numbers_are_identical(
        self,
    ) -> None:
        """The sharpest one. Same K value, no source — every dimension unchanged.

        The blank, the solid, the mass and the reconciliation all still pass. What
        fails is that nobody can say where the number came from, which is the whole
        argument of `app/sheetmetal/kfactor.py` and of Decision 3.
        """
        result = _run(
            _variant(k=assumed(m._M3_K_FACTOR.value, why="no test bend has been made"))
        )

        assert result.outcome is MissionOutcome.FAILED
        assert _failed_claims(result) == {
            "every bend's K-factor names a source",
            "every forming check had the data it needed",
        }
        payload = _payload_of(result)
        assert payload["flat"]["flat_length_mm"] == pytest.approx(
            FLAT_LENGTH_MM, rel=1e-12
        )
        assert payload["volume_mm3"] == pytest.approx(VOLUME_MM3, rel=1e-9)
        assert result.checks is not None
        assert result.checks.approximate, (
            "an assumed K makes every number derived from the blank an estimate, and "
            "the provenance sidecar is what carries that into the report"
        )

    def test_a_solid_drawn_to_a_different_radius_breaks_the_reconciliation(self) -> None:
        """The two descriptions of the part quietly stop describing the same part.

        Nothing refuses it. The fold tree still unfolds, the blank is still a blank,
        the solid still builds and still measures. The residual is the only thing that
        notices — which is why it is published and claimed on.
        """
        result = _run(_variant(drawn_radius_mm=3.0))

        assert result.outcome is MissionOutcome.FAILED
        assert result.folded is not None
        assert result.folded.pattern is not None, "the blank is untouched"
        assert result.folded.formability is not None
        assert result.folded.formability.ok, "and it is still perfectly formable"
        assert "the blank accounts for the solid" in _failed_claims(result)
        assert _payload_of(result)["flat"]["volume_mismatch_mm3"] > 100.0

    def test_a_blank_that_does_not_yield_the_run_fails_the_budget(self) -> None:
        result = _run(_variant(stock=StockSheet("offcut", length_mm=1000.0, width_mm=300.0)))

        assert result.outcome is MissionOutcome.FAILED
        assert _failed_claims(result) == {"the sheet yields the run it has to"}
        assert _payload_of(result)["nest"]["parts_per_sheet"] == 4.0

    def test_a_blank_too_wide_for_the_sheet_says_which_way_it_did_not_fit(self) -> None:
        result = _run(_variant(stock=StockSheet("strip", length_mm=2000.0, width_mm=100.0)))

        assert result.outcome is MissionOutcome.FAILED
        assert _failed_claims(result) == {
            "the sheet yields the run it has to",
            "the blank fits across the sheet",
        }

    def test_no_stock_is_unmeasured_and_never_a_pass(self) -> None:
        """A blank with no sheet has no yield — which is not the same as no waste."""
        result = _run(_variant(stock=None))

        assert result.outcome is MissionOutcome.FAILED
        assert _unmeasured_claims(result) == {
            "the sheet yields the run it has to",
            "the blank fits across the sheet",
            "the blank fits along the sheet",
        }
        assert not _failed_claims(result)
        assert "no stock sheet was declared" in result.reason

    def test_a_build_that_stops_is_a_failure_naming_where(self) -> None:
        """The rung declared it builds; whatever stopped it, the declaration is false."""

        def broken(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            raise RuntimeError("the sketch would not close")

        result = run_mission(mission("M3"), broken)

        assert result.outcome is MissionOutcome.FAILED
        assert "the build stopped" in result.reason
        assert result.folded is not None
        assert result.folded.pattern is not None, "the blank flattened before the build"
        assert result.folded.formability is None


# --------------------------------------------------------------------------
# What the packages could not express
# --------------------------------------------------------------------------


class TestTheGapsThisRungFound:
    """Pinned so that the day one closes, a test says so rather than nobody noticing."""

    def test_the_kernel_has_no_sheet_metal_operation(self) -> None:
        """Which is why `_m3_spec` draws the folded section by hand in twenty segments.

        If this fails, a sheet-metal feature has landed in the OCCT backend and M3
        should be rewritten to use it: the hand-drawn cross-section is a workaround,
        not a design, and it is the reason the part has to be declared twice.
        """
        from app.kernel import OcctRunner

        words = ("sheet", "wall", "flange", "bend", "unfold", "hem", "stamp")
        offered = [
            tool
            for tool in OcctRunner.supported_tools()
            if any(word in tool for word in words)
        ]

        assert offered == [], (
            "the backend now offers "
            f"{offered} — see this test's docstring before deleting it"
        )

    def test_a_sheet_metal_part_cannot_produce_a_design_spec(self) -> None:
        """The other half of the same gap, from the sheet-metal side."""
        part = m._m3_part()

        assert not hasattr(part, "to_spec")
        assert not isinstance(part, DesignSpec)

    def test_the_flat_pattern_publishes_no_measurement_payload(self) -> None:
        """`to_dict()` is a serialisation for a reader, not a mapping a path can walk.

        Which is why `_flat_payload` selects and flattens, and why it is the mission
        rather than the package that attaches provenance to the result.
        """
        from app.sheetmetal import unfold

        pattern = unfold(m._m3_part())
        data = pattern.to_dict()

        assert "provenance" not in data
        assert isinstance(data["faces"], list), "nested, not a flat measurement mapping"

    def test_the_package_has_no_stock_and_no_nest(self) -> None:
        """`StockSheet` and `_nest` live in `missions.py` because nothing else has them."""
        import app.sheetmetal as sheetmetal

        assert not hasattr(sheetmetal, "StockSheet")
        assert not hasattr(sheetmetal, "nest")

    def test_the_outer_fibre_strain_is_reported_and_compared_to_nothing(self) -> None:
        """38% on this bend, and no grade in the package carries an elongation.

        That is the sharp version of the gap. The cover passes the minimum bend radius
        with half a millimetre in hand, and the outside of every bend is asked to
        stretch 38% — which is at or past what cold-rolled mild steel gives. The number
        is computed, printed in the passing finding's own message, and compared to
        nothing, because `SheetMaterial` has no elongation to compare it to. The
        minimum radius is the only gate on cracking, so a supplier figure that happens
        to be optimistic passes a bend the material will not take.
        """
        sheet = sheet_material("steel_mild_cr", thickness_mm=T_MM)
        strain = sheet.outer_fibre_strain(inside_radius_mm=R_MM, k=K)

        assert 0.30 < strain < 0.45

        # And the reported strain is a function of K, which is the number with no
        # test bend behind it: the same physical bend reads 38% on DIN's K and 31%
        # on Machinery's Handbook's. So the one figure an engineer would act on is
        # itself sensitive to the judgement `_M3_UNPROVEN` says is unproven.
        ansi = sheet.outer_fibre_strain(
            inside_radius_mm=R_MM, k=m._M3_ALTERNATIVE_K.value
        )
        assert abs(strain - ansi) > 0.05
        assert not hasattr(sheet, "elongation")
        result = _run()
        assert result.folded is not None
        assert result.folded.formability is not None
        assert not [
            finding
            for finding in result.folded.formability.findings
            if "strain" in finding.check
        ]

    def test_nothing_in_the_model_can_say_which_way_the_grain_runs(self) -> None:
        """The caveat on every shipped minimum bend radius, unrepresentable."""
        sheet = sheet_material("steel_mild_cr", thickness_mm=T_MM)
        bend = Bend(
            angle_deg=90.0,
            inside_radius_mm=R_MM,
            direction=BendDirection.DOWN,
            k=m._M3_K_FACTOR,
        )

        assert "across the grain" in (sheet.minimum_bend_radius or object()).note  # type: ignore[union-attr]
        assert not hasattr(sheet, "grain_direction")
        assert not hasattr(bend, "grain_direction")
        assert any("grain" in caveat for caveat in m._M3_UNPROVEN)


# --------------------------------------------------------------------------
# Spelling, pinned
# --------------------------------------------------------------------------


class TestTheSpellingsMatchTheirSources:
    """`missions.py` spells these rather than importing the kernel; a rename must show."""

    def test_the_provenance_vocabulary(self) -> None:
        from app.kernel import provenance

        assert m._PROVENANCE_KEY == provenance.PROVENANCE_KEY
        assert m._BASIS_MEASURED == str(provenance.Basis.MEASURED)
        assert m._BASIS_APPROXIMATED == str(provenance.Basis.APPROXIMATED)
        assert m._BASIS_UNAVAILABLE == str(provenance.Basis.UNAVAILABLE)

    def test_the_formability_check_names_slug_to_the_paths_m3_claims_on(self) -> None:
        """A check renamed in `app.sheetmetal` leaves the claim on it UNMEASURED."""
        claimed = {
            a.measure
            for a in m._M3_ASSERTIONS
            if a.measure.startswith("formability.") and a.measure.endswith("_margin_mm")
        }
        result = _run()
        available = set(_payload_of(result)["formability"])

        assert claimed == {f"formability.{name}" for name in available if name.endswith("_margin_mm")} - {
            "formability.worst_margin_mm"
        } | claimed
        for measure in claimed:
            assert measure.split(".", 1)[1] in available, measure

    def test_an_unmeasured_claim_is_never_a_pass(self) -> None:
        """The rule, one level down, on the path that carries it here."""
        result = _run(_variant(stock=None))

        assert result.checks is not None
        for check in result.checks.results:
            if check.outcome is Outcome.UNMEASURED:
                assert not check.passed
