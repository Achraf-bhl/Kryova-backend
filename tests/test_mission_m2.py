"""M2 — the welded frame. The ladder's first rung that is a product, not a part.

Master plan 5.4 and Phase 14. M1 proved a spec compiles and builds; M2 is the first
mission where the interesting question is not "did the geometry come out" but "do
these things fit together, and is the number that says so one anybody can trace".

Three halves, and the split is the one `app/design/` keeps everywhere.

* **Declaration** — what a product rung is, and the four ways declaring one can be
  dishonest. Pure, offline, milliseconds.
* **What could not be measured** — a build with no geometry behind it, which must
  come back UNMEASURED and red rather than green. Also offline: it is the case where
  there is nothing to measure, so nothing needs a kernel to prove it.
* **The frame itself** — built on OCCT, every number checked against arithmetic
  written out here rather than against what the kernel said last time, and then the
  same frame built **wrong** six ways to watch each guard fail. A mission that cannot
  fail is a mission that proves nothing, so every claim M2 makes is broken here on
  purpose at least once.

The kernel is imported inside the tests that need it, the way `test_design_missions.py`
and `test_kernel.py` do, so collecting this file does not drag ~166 MB of OCP into
every run.
"""

from __future__ import annotations

import math
import pathlib
import subprocess
import sys
from collections.abc import Mapping
from typing import Any

import pytest

from app.assembly.contracts import bind_into
from app.assembly.errors import ContractError
from app.assembly.structure import StructureBuilder
from app.design import missions as m
from app.design.assertions import Assertion, Outcome
from app.design.errors import SpecError
from app.design.missions import (
    AssemblyDesign,
    LadderReport,
    Mission,
    MissionOutcome,
    MissionResult,
    mission,
    run_mission,
)
from app.design.params import Parameter, Unit
from app.design.spec import DesignSpec

# --------------------------------------------------------------------------
# The arithmetic, written out independently.
#
# Every number below is computed here from the drawing, never read from
# `app.design.missions`. A test that derives its expectation from the thing it is
# checking agrees with any mistake that thing makes — and a frame built to the wrong
# section and a frame built to the right one are the same picture.
# --------------------------------------------------------------------------

SECTION_AREA_MM2 = 60.0 * 40.0 - 52.0 * 32.0  # RHS 60x40x4: outer less bore
POST_VOLUME_MM3 = SECTION_AREA_MM2 * 700.0
HEADER_VOLUME_MM3 = SECTION_AREA_MM2 * 800.0
DENSITY_KG_M3 = 7870.0  # steel-1018

POST_MASS_KG = POST_VOLUME_MM3 * 1e-9 * DENSITY_KG_M3
HEADER_MASS_KG = HEADER_VOLUME_MM3 * 1e-9 * DENSITY_KG_M3
FRAME_MASS_KG = 2.0 * POST_MASS_KG + HEADER_MASS_KG

#: Two posts at half their height, the header on its own axis half a section above
#: the posts' tops. Mass-weighted, not the mean of three heights.
CENTRE_Z_MM = (
    2.0 * POST_MASS_KG * 350.0 + HEADER_MASS_KG * (700.0 + 30.0)
) / FRAME_MASS_KG


def _variant(**overrides: Any) -> Mission:
    """M2, with the frame built to a different drawing. The break harness.

    Carries M2's own assertions and its own contract, so what is being tested is
    whether the *shipped* claims catch the fault — not whether a claim written for
    the occasion does.
    """
    return Mission(
        rung="M2",
        title="Welded frame / bench",
        era="III",
        hard="Weld sizing, fatigue at joints",
        assembly=m._m2_design(**overrides),
        assertions=m._M2_ASSERTIONS,
        unproven=m._M2_UNPROVEN,
    )


def _run(rung: Mission = None) -> MissionResult:  # type: ignore[assignment]
    from app.kernel import OcctRunner

    return run_mission(rung or mission("M2"), runner_factory=OcctRunner)


def _failed_claims(result: MissionResult) -> set[str]:
    """Names of the mission's own assertions that came back FAILED."""
    if result.checks is None:
        return set()
    return {r.name for r in result.checks.failed}


def _unmeasured_claims(result: MissionResult) -> set[str]:
    if result.checks is None:
        return set()
    return {r.name for r in result.checks.unmeasured}


def _violated_interface_claims(result: MissionResult) -> set[str]:
    if result.assembly is None:
        return set()
    return {v.claim for v in result.assembly.violations}


class TestTheFrameIsDeclaredAsAProduct:
    """A rung that is more than one part, said in `app.assembly`'s own vocabulary."""

    def test_m2_is_buildable_and_is_an_assembly(self) -> None:
        rung = mission("M2")

        assert rung.buildable
        assert rung.is_assembly
        assert rung.spec is None, "a product rung has no single part design"
        assert rung.needs == (), "a rung that builds is not waiting on anything"

    def test_two_components_stand_for_three_members(self) -> None:
        """The graph earning its keep: the post is designed once and placed twice."""
        structure = mission("M2").assembly.structure

        assert structure.quantities() == {"frame": 1, "post": 2, "header": 1}
        assert structure.occurrence_count() == 3
        assert len(structure.bill_of_materials()) == 2

    def test_the_occurrence_paths_are_declared_not_positional(self) -> None:
        """`post.2` is the post the author called 2 — the rule a clash exclusion rests on."""
        paths = [o.path for o in mission("M2").assembly.structure.occurrences()]

        assert paths == ["frame/post.1", "frame/post.2", "frame/header.1"]

    def test_the_section_is_declared_once_and_bound_into_both_members(self) -> None:
        """14.3: two copies of a number agree the day they are typed and never after."""
        design = mission("M2").assembly
        raw = m._m2_member_spec("bare", 100.0)

        assert not any(p.name == "section_depth_mm" for p in raw.parameters), (
            "a member must not declare the section itself — that is the second copy"
        )
        for part in ("post", "header"):
            values = {p.name: p.value for p in design.parts[part].parameters}
            assert values["section_depth_mm"] == 60.0
            assert values["section_width_mm"] == 40.0
            assert values["wall_mm"] == 4.0

    def test_a_member_that_redeclares_the_section_is_refused_with_both_parties(
        self,
    ) -> None:
        """The compile error at the interface, three weeks before it is a clash."""
        rogue = DesignSpec.of(
            "M2 post",
            parameters=[Parameter("section_depth_mm", Unit.MM, value=50.0)],
            features=list(m._m2_member_spec("x", 700.0).features),
        )

        with pytest.raises(ContractError) as exc:
            bind_into(m._M2_JOINT, rogue)

        assert "post" in str(exc.value) and "header" in str(exc.value)

    def test_the_joint_is_one_contract_covering_both_places_they_meet(self) -> None:
        """Two joints, one contract. `_boundary_payload` insists on measuring both."""
        interface = mission("M2").assembly.interfaces[0]

        assert interface.provider == "post"
        assert interface.consumer == "header"
        assert interface.counterparty("post") == "header"

    def test_the_clash_check_is_not_told_to_ignore_the_joints(self) -> None:
        """`touching_components` would exclude exactly the pairs being verified."""
        assert mission("M2").assembly.ignore is None
        assert mission("M2").assembly.clearance_mm > 0.0


class TestADeclarationCannotBeHalfHonest:
    """Each guard on the declaration, verified by writing the thing it refuses."""

    def test_a_rung_cannot_be_a_part_and_a_product(self) -> None:
        with pytest.raises(SpecError, match="both a part design and an assembly"):
            Mission(
                rung="M2",
                title="t",
                era="III",
                hard="h",
                spec=mission("M1").spec,
                assembly=mission("M2").assembly,
                assertions=mission("M1").assertions,
            )

    def test_a_pending_rung_cannot_say_what_it_leaves_unproven(self) -> None:
        """Nothing about an unattempted rung is proven; that is what `needs` says."""
        with pytest.raises(SpecError, match="belong in `needs`"):
            Mission(
                rung="M3",
                title="t",
                era="IV",
                hard="h",
                needs=("E17.3 — sheet metal",),
                unproven=("E6 — no solver",),
            )

    def test_a_leaf_with_no_design_is_refused(self) -> None:
        """A part nobody built is a hole in the mass and a hole in the clash check."""
        with pytest.raises(SpecError, match="no design to build it"):
            AssemblyDesign(
                structure=mission("M2").assembly.structure,
                parts={"post": m._m2_member_spec("p", 700.0)},
            )

    def test_a_design_for_something_not_in_the_product_is_refused(self) -> None:
        parts = dict(mission("M2").assembly.parts)
        parts["gusset"] = m._m2_member_spec("gusset", 100.0)

        with pytest.raises(SpecError, match="not a leaf of the product structure"):
            AssemblyDesign(structure=mission("M2").assembly.structure, parts=parts)

    def test_a_component_named_after_a_payload_key_is_refused(self) -> None:
        """Otherwise an assertion on the frame's mass reads one member's, and passes."""
        builder = StructureBuilder()
        builder.define("frame")
        builder.define("mass_kg")
        builder.add("frame", "mass_kg")

        with pytest.raises(SpecError, match="share a name with a key"):
            AssemblyDesign(
                structure=builder.build("frame"),
                parts={"mass_kg": m._m2_member_spec("x", 100.0)},
            )


class TestEveryPartGetsItsOwnDocument:
    """Two members in one document is a frame that weighs what one tube weighs."""

    def test_an_assembly_rung_refuses_a_single_runner(self) -> None:
        def runner(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            return {}

        with pytest.raises(SpecError, match="runner_factory"):
            run_mission(mission("M2"), runner)

    def test_the_factory_is_called_once_per_part(self) -> None:
        made: list[Any] = []

        def factory() -> Any:
            def runner(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(_FAKE_PAYLOAD)

            made.append(runner)
            return runner

        run_mission(mission("M2"), runner_factory=factory)

        assert len(made) == 2, "one runner for the post, one for the header"
        assert made[0] is not made[1]

    def test_a_buildable_rung_with_nothing_to_build_with_is_a_caller_error(self) -> None:
        with pytest.raises(SpecError, match="needs something to build with"):
            run_mission(mission("M2"))


#: A payload shaped like a real measurement, from a runner holding no geometry. Enough
#: for the plan to run and the mass to roll up; nothing a distance can be measured from.
_FAKE_PAYLOAD: Mapping[str, Any] = {
    "feature": "Thing.1",
    "mass_kg": 1.0,
    "centre_of_mass_mm": [0.0, 0.0, 0.0],
    "volume_mm3": 1.0,
    "bounding_box_mm": {"min": [0.0, 0.0, 0.0], "size": [1.0, 1.0, 1.0]},
    "face_count": 10,
    "solid_count": 1,
}


class TestWhatCouldNotBeMeasuredIsNeverAPass:
    """The rule the whole ladder exists for, at the level of a machine.

    A runner that builds but holds no geometry is what a CATIA-backed runner looks
    like from here, and what a broken OCCT install looks like too. The frame's mass
    still rolls up — every backend returns mass — and nothing that needs a *distance*
    can be answered. That must come back unmeasured and red, never green and quiet.
    """

    @staticmethod
    def _result() -> MissionResult:
        return run_mission(
            mission("M2"),
            runner_factory=lambda: (lambda tool, arguments: dict(_FAKE_PAYLOAD)),
        )

    def test_the_rung_fails_rather_than_passing_on_what_it_could_not_see(self) -> None:
        assert self._result().outcome is MissionOutcome.FAILED

    def test_the_envelope_is_unmeasured_and_says_why(self) -> None:
        result = self._result()

        assert "the frame is as wide as its span" in _unmeasured_claims(result)
        reason = next(
            r.reason
            for r in result.checks.results
            if r.name == "the frame is as wide as its span"
        )
        assert "bounding box" in reason

    def test_the_joint_is_unmeasured_not_violated(self) -> None:
        """Two different recoveries: go and measure it, versus go and change it."""
        result = self._result()
        outcomes = {v.claim: v.outcome for v in result.assembly.violations}

        assert outcomes["the joint closes everywhere it is made"] is Outcome.UNMEASURED

    def test_the_pairs_nobody_looked_at_are_counted_and_named(self) -> None:
        result = self._result()

        assert not result.assembly.clash.complete
        assert len(result.assembly.clash.unchecked) == 3
        assert "no shape was supplied" in result.assembly.clash.unchecked[0].reason


class TestARungThatBuildsIsNotARungThatIsFinished:
    """`unproven` — the difference between "the frame builds" and "M2 is done"."""

    @staticmethod
    def _passed(unproven: tuple[str, ...]) -> MissionResult:
        rung = Mission(
            rung="M2",
            title="Welded frame / bench",
            era="III",
            hard="h",
            spec=mission("M1").spec,
            assertions=mission("M1").assertions,
            unproven=unproven,
        )
        return MissionResult(mission=rung, outcome=MissionOutcome.PASSED)

    def test_m2_names_the_analysis_nobody_has_run(self) -> None:
        caveats = " ".join(mission("M2").unproven)

        assert "E6" in caveats and "E8.3" in caveats and "E17.4" in caveats
        assert "weld" in caveats

    def test_a_caveated_pass_is_still_a_pass(self) -> None:
        report = LadderReport(results=(self._passed(("E6 — no solver",)),))

        assert report.ok, "the geometry it checked is checked; that is not a failure"

    def test_but_the_ladder_is_not_complete_while_one_stands(self) -> None:
        report = LadderReport(results=(self._passed(("E6 — no solver",)),))

        assert not report.complete
        assert len(report.caveated) == 1

    def test_the_caveat_is_printed_beside_the_pass(self) -> None:
        """The only line between "M2 passed" and "the welds are sized"."""
        summary = LadderReport(results=(self._passed(("E6 — no solver",)),)).summary()

        assert "not claimed" in summary
        assert "E6" in summary


class TestTheFrameBuilds:
    """The rung itself, on the real kernel. What 5.4 exists for."""

    def test_the_frame_builds_and_every_claim_holds(self) -> None:
        result = _run()

        assert result.outcome is MissionOutcome.PASSED, str(result)
        assert result.checks is not None
        assert len(result.checks.passed) == len(mission("M2").assertions)
        assert result.checks.unmeasured == (), "a claim nobody measured is not a pass"

    def test_the_joint_contract_holds_at_both_joints(self) -> None:
        result = _run()

        assert len(result.assembly.contracts) == 1
        assert result.assembly.contracts[0].ok
        assert result.assembly.violations == ()

    def test_the_post_was_built_once_and_placed_twice(self) -> None:
        """Two components, three occurrences — measured, not asserted in a docstring."""
        result = _run()

        assert [name for name, _ in result.assembly.builds] == ["post", "header"]
        assert result.assembly.mass.occurrences == 3
        assert result.assembly.mass.components_measured == 2

    def test_the_frame_weighs_its_closed_form(self) -> None:
        """12.743 kg of 1018 steel, computed here from the section and the lengths."""
        result = _run()

        assert result.assembly.mass.mass_kg == pytest.approx(FRAME_MASS_KG, rel=1e-9)
        assert result.assembly.payload["mass_kg"] == pytest.approx(
            FRAME_MASS_KG, rel=1e-9
        )

    def test_the_centre_of_mass_is_where_three_members_put_it(self) -> None:
        centre = _run().assembly.mass.centre_of_mass_mm

        assert centre[0] == pytest.approx(400.0, abs=1e-3), "symmetric about mid-span"
        assert centre[2] == pytest.approx(CENTRE_Z_MM, abs=1e-3)

    def test_the_members_are_hollow_and_not_bars(self) -> None:
        """A solid bar of the same section builds, fits and welds, and weighs 2.3x."""
        payload = _run().assembly.payload

        assert payload["post"]["volume_mm3"] == pytest.approx(POST_VOLUME_MM3, rel=1e-9)
        assert payload["header"]["volume_mm3"] == pytest.approx(
            HEADER_VOLUME_MM3, rel=1e-9
        )
        assert payload["post"]["face_count"] == 10

    def test_the_envelope_is_the_frame_a_drawing_would_show(self) -> None:
        size = _run().assembly.payload["envelope_mm"]["size"]

        assert size[0] == pytest.approx(800.0, abs=1e-3)
        assert size[1] == pytest.approx(40.0, abs=1e-3)
        assert size[2] == pytest.approx(760.0, abs=1e-3)

    def test_it_builds_the_same_frame_twice(self) -> None:
        """Determinism (1.6) at the level a mission cares about."""
        first, second = _run(), _run()

        assert [r.plan_digest for _, r in first.assembly.builds] == [
            r.plan_digest for _, r in second.assembly.builds
        ]
        assert first.assembly.mass.mass_kg == pytest.approx(
            second.assembly.mass.mass_kg, rel=1e-12
        )


class TestTheLadderItself:
    """What the whole ladder says once M2 is on it. The headline nobody may misread."""

    @staticmethod
    def _ladder() -> Any:
        from app.design.missions import run_ladder
        from app.kernel import OcctRunner

        return run_ladder(OcctRunner)

    def test_four_rungs_pass_and_five_are_not_yet_buildable(self) -> None:
        """M6 joined on 2026-09-10, when E14.1 and E12.3 — its two declared
        needs — were both complete. The coverage figure is measured here rather
        than asserted anywhere, so it moves deliberately."""
        report = self._ladder()

        assert [r.rung for r in report.passed] == ["M1", "M2", "M3", "M6"]
        assert len(report.pending) == 5
        assert report.ok, report.summary()

    def test_the_ladder_is_not_complete_even_though_nothing_failed(self) -> None:
        """Five rungs unclimbed, M2's welds unsized, M3's K never bent and M6's
        belt never loaded. `ok` is the regression question; `complete` is the
        programme question, and they are not the same."""
        report = self._ladder()

        assert not report.complete
        assert [r.rung for r in report.caveated] == ["M2", "M3", "M6"]

    def test_the_sentence_a_human_reads_names_both(self) -> None:
        summary = self._ladder().summary()

        assert "4/9 rungs pass" in summary
        assert "5 not yet buildable" in summary
        assert "not claimed" in summary and "weld" in summary


class TestEveryNumberIsTraceable:
    """A mission is not done when the geometry is right — only when the numbers are."""

    def test_every_pair_was_looked_at_or_soundly_rejected(self) -> None:
        clash = _run().assembly.clash

        assert clash.pairs_total == 3
        assert clash.narrow_checked == 2, "both joints measured"
        assert len(clash.rejected_by_bounds) == 1, "the two posts, 680 mm apart"
        assert clash.unchecked == ()
        assert clash.complete

    def test_the_minimum_clearance_is_published_because_the_check_is_complete(
        self,
    ) -> None:
        """An incomplete check publishes it as UNAVAILABLE — the honest direction."""
        payload = _run().assembly.payload

        assert payload["clash"]["minimum_clearance_mm"] == pytest.approx(0.0, abs=1e-6)
        record = payload["provenance"]["clash.minimum_clearance_mm"]
        assert record["basis"] == "measured"

    def test_the_envelope_says_how_it_was_arrived_at(self) -> None:
        payload = _run().assembly.payload

        assert payload["provenance"]["envelope_mm.size[1]"]["basis"] == "measured"
        assert "over-estimate" in payload["provenance"]["envelope_mm"]["method"]

    def test_no_claim_was_checked_against_an_approximated_number(self) -> None:
        """Mass is integrated; nothing here is ray cast. If that changes, say so."""
        assert _run().checks.approximate is False

    def test_the_paths_are_the_kernel_s_own_spellings(self) -> None:
        """Spelled in `missions.py` to keep OCP out of it; pinned here so a rename
        there cannot orphan them in silence."""
        from app.kernel import interrogation, provenance

        assert m._MINIMUM_CLEARANCE_MM == interrogation.MINIMUM_CLEARANCE_MM
        assert m._INTERFERENCE_VOLUME_MM3 == interrogation.INTERFERENCE_VOLUME_MM3
        assert m._PROVENANCE_KEY == provenance.PROVENANCE_KEY
        assert m._BASIS_MEASURED == provenance.Basis.MEASURED.value
        assert m._BASIS_UNAVAILABLE == provenance.Basis.UNAVAILABLE.value

    def test_the_reserved_keys_are_the_keys_the_payload_actually_publishes(self) -> None:
        """The collision guard is only worth having if it lists the real collisions."""
        payload = _run().assembly.payload
        published = {key for key in payload if key not in {"post", "header"}}

        assert published <= m._RESERVED_PAYLOAD_KEYS, (
            "the combined payload grew a top-level key the component-name guard does "
            "not know about, so a component could be named after it and shadow it"
        )

    def test_the_result_serialises_with_its_numbers_and_its_caveats(self) -> None:
        data = _run().to_dict()

        assert data["outcome"] == "passed"
        assert any("E6" in caveat for caveat in data["unproven"])
        assert set(data["assembly"]["builds"]) == {"post", "header"}
        assert data["assembly"]["measurements"]["mass_kg"] == pytest.approx(
            FRAME_MASS_KG, rel=1e-9
        )


class TestBreakingTheFrameFailsTheRung:
    """Six wrong frames. A guard nobody has watched fail has not been verified.

    Each case names the claim that must catch it, because "it went red" is not
    evidence the right thing went red — a mission whose mass assertion catches every
    fault has one assertion and seventeen decorations.
    """

    def test_a_post_cut_ten_millimetres_short_opens_the_joint(self) -> None:
        result = _run(_variant(post_mm=690.0))

        assert result.outcome is MissionOutcome.FAILED
        assert "the joint closes everywhere it is made" in _violated_interface_claims(
            result
        )
        assert "the post is the length it was cut to" in _failed_claims(result)

    def test_the_open_joint_is_seen_at_the_second_joint_not_only_the_first(self) -> None:
        """The whole reason the boundary reports the *widest* gap and not the minimum.

        Both posts are one component here, so both are short — but the number that
        catches it is the worst joint, and `minimum_clearance_mm` at this interface
        would still read 0 mm the moment either post touched.
        """
        result = _run(_variant(post_mm=690.0))
        violation = next(
            v
            for v in result.assembly.violations
            if v.claim == "the joint closes everywhere it is made"
        )

        assert violation.result.measured == pytest.approx(10.0, abs=1e-3)
        assert violation.provider == "post" and violation.consumer == "header"

    def test_a_header_set_ten_millimetres_low_interpenetrates_the_posts(self) -> None:
        structure = m._m2_structure(header_axis_z_mm=720.0)
        result = _run(_variant(structure=structure))

        assert result.outcome is MissionOutcome.FAILED
        assert (
            "the members meet without occupying the same space"
            in _violated_interface_claims(result)
        )
        assert "nothing in the frame clashes" in _failed_claims(result)
        assert result.assembly.clash.worst_interference_mm3 > 0.0

    def test_a_solid_bar_where_a_tube_was_specified_fails_on_mass(self) -> None:
        """It builds, it fits, it welds, and it is 2.3 times the frame's weight."""
        result = _run(_variant(hollow=False))

        assert result.outcome is MissionOutcome.FAILED
        assert "the frame weighs its closed form" in _failed_claims(result)
        assert "the frame is inside its mass budget" in _failed_claims(result)
        assert "the post is a closed tube" in _failed_claims(result)

    def test_the_header_section_rolled_a_quarter_turn_fails_on_the_envelope(
        self,
    ) -> None:
        """Every member is the right size and the frame is the wrong shape."""
        structure = m._m2_structure(header_roll_rad=math.pi / 2.0)
        result = _run(_variant(structure=structure))

        assert result.outcome is MissionOutcome.FAILED
        assert "the frame is one section thick out of plane" in _failed_claims(result)

    def test_a_header_cut_short_fails_on_its_own_length_and_the_mass(self) -> None:
        """It still bears on both posts, so the joint is silent and the member is not."""
        result = _run(_variant(span_mm=790.0))

        assert result.outcome is MissionOutcome.FAILED
        assert "the header is the length it was cut to" in _failed_claims(result)
        assert _violated_interface_claims(result) == set(), (
            "the joint really does still close — this fault is the member's, and the "
            "contract should not be reported as violated for it"
        )

    def test_a_contact_only_clash_check_reports_the_joint_as_unmeasured(self) -> None:
        """The reason M2 inspects to 25 mm rather than to contact.

        With `clearance_mm=0` the broad phase throws away the pair whose boxes are 10
        mm apart — soundly, for a clash question — and the fit-up claim then has
        nothing to read. It must come back UNMEASURED, not passed: this is the case
        where a check that "found no clash" is a check that found nothing.
        """
        result = _run(_variant(post_mm=690.0, clearance_mm=0.0))
        outcomes = {v.claim: v.outcome for v in result.assembly.violations}

        assert result.outcome is MissionOutcome.FAILED
        assert outcomes["the joint closes everywhere it is made"] is Outcome.UNMEASURED
        assert result.assembly.clash.clashes == ()

    def test_a_part_that_does_not_build_stops_the_rung_and_names_it(self) -> None:
        """Before any of the above: a clash check over two members and a hole is a
        check of a different machine."""

        def broken() -> Any:
            def runner(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
                if tool == "catia_pocket":
                    raise RuntimeError("the profile is not closed")
                return dict(_FAKE_PAYLOAD)

            return runner

        result = run_mission(mission("M2"), runner_factory=broken)

        assert result.outcome is MissionOutcome.FAILED
        assert "post did not build" in result.reason
        assert "profile is not closed" in result.reason
        assert result.assembly.clash is None, "nothing was measured, so nothing is claimed"


class TestTheLadderSurvivesImportOrder:
    """M2 made `app.design` depend on `app.assembly`, which depends back on it.

    `app.assembly.structure` imports `app.design.names`, which executes
    `app/design/__init__.py`; before this was found, that file imported `missions`,
    which imports `app.assembly.clash` — so `import app.assembly` first blew up with
    an ImportError and `import app.design` first did not. A failure that depends on
    which module a process happens to reach first is the worst kind: this suite
    imported in the lucky order and was green while `python -c "import app.assembly"`
    was broken. The re-export is lazy now (PEP 562), and this runs a fresh interpreter
    in each order because within one process the damage is already done or already
    avoided by the time a test could look.
    """

    @staticmethod
    def _import(statement: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", statement],
            cwd=str(pathlib.Path(__file__).resolve().parent.parent),
            capture_output=True,
            text=True,
            check=False,
        )

    @pytest.mark.parametrize(
        "statement",
        [
            "import app.assembly, app.design.missions",
            "import app.design.missions, app.assembly",
            "import app.design; app.design.LADDER",
            "from app.design import run_ladder, LADDER",
        ],
    )
    def test_the_packages_import_in_either_order(self, statement: str) -> None:
        result = self._import(statement)

        assert result.returncode == 0, result.stderr

    def test_the_lazy_re_export_still_refuses_a_name_that_is_not_there(self) -> None:
        """A module `__getattr__` that returns something for everything hides typos."""
        import app.design

        with pytest.raises(AttributeError, match="no attribute"):
            app.design.definitely_not_exported  # noqa: B018


class TestTheAssertionsAreNotDecorations:
    """Every claim M2 makes must be able to fail; a claim that cannot is noise."""

    def test_every_assertion_has_a_measurable_path_and_a_tolerance_that_is_sane(
        self,
    ) -> None:
        for assertion in m._M2_ASSERTIONS:
            assert isinstance(assertion, Assertion)
            assert assertion.tolerance >= 0.0
            if assertion.comparison == "==" and assertion.tolerance == 0.0:
                assert assertion.measure.endswith("_count"), (
                    f"{assertion.name}: an exact equality on a measured number will "
                    "fail on a part that is right"
                )

    def test_the_frame_claims_more_than_its_mass(self) -> None:
        """A rung whose only real claim is one number is a rung with one test."""
        paths = {a.measure.split(".")[0].split("[")[0] for a in m._M2_ASSERTIONS}

        assert {"mass_kg", "clash", "envelope_mm", "post", "header"} <= paths
