"""Process rule sets attach from the part's features, and a missing limit is never a pass (E13.1, E13.4).

Offline: the measurements are dicts shaped like the kernel's payloads, so what is tested is
the attachment and the verdict, not the scans (those are `tests/test_interrogation.py`'s).
The route that runs the scans on a live part is tested in `tests/test_kernel_routes.py`.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import pytest

from app.design.assertions import Outcome
from app.kernel import provenance
from app.rules.errors import RuleError, SourceError
from app.rules.processes import RULE_SETS, Limit, Process, attach, check

GUIDE = "test fixture: a design guide nobody published"
PLATE = ("catia_pad",)
POCKETED = ("catia_pad", "catia_pocket")


def _limits(**values: float) -> dict[str, Limit]:
    return {key: Limit(value=value, source=GUIDE) for key, value in values.items()}


class TestEveryProcessHasASet:
    def test_the_six_processes_the_plan_names_each_have_one(self) -> None:
        assert {p.value for p in RULE_SETS} == {
            "cast",
            "machined",
            "printed",
            "sheet",
            "moulded",
            "welded",
        }

    @pytest.mark.parametrize("process", list(Process))
    def test_every_template_names_a_quantity_the_kernel_measures(self, process: Process) -> None:
        from app.rules.vocabulary import is_measurable

        for template in RULE_SETS[process].templates:
            assert is_measurable(template.measure), template.measure

    def test_the_sets_that_cannot_check_everything_say_what_they_skip(self) -> None:
        assert RULE_SETS[Process.SHEET].not_checked_here
        assert "app/sheetmetal" in RULE_SETS[Process.SHEET].not_checked_here[0]
        assert "E17 task 3" in RULE_SETS[Process.WELDED].not_checked_here[0]

    def test_no_template_carries_a_number(self) -> None:
        """The limits are a supplier's; a default here would be a remembered figure."""
        for rule_set in RULE_SETS.values():
            for template in rule_set.templates:
                assert not hasattr(template, "limit")
                assert not hasattr(template, "value")


class TestAttachmentReadsTheFeatures:
    def test_a_pocket_brings_the_cutter_radius_rule_and_a_plain_plate_does_not(self) -> None:
        limits = _limits(minimum_inside_radius=3.0)
        plain = attach("machined", PLATE, limits)
        pocketed = attach("machined", POCKETED, limits)

        assert "minimum_inside_radius" in plain.not_applicable
        assert [r.name for r in pocketed.rules] == ["machined.minimum_inside_radius"]

    def test_a_rule_the_process_needs_with_no_limit_is_unset_not_dropped(self) -> None:
        attached = attach("cast", PLATE, _limits(minimum_wall=3.0))
        unset = {u.key for u in attached.unset}
        assert unset == {"minimum_draft", "undercuts", "minimum_inside_radius"}

    def test_the_scans_the_rules_need_are_named(self) -> None:
        attached = attach(
            "cast",
            PLATE,
            _limits(minimum_wall=3.0, minimum_draft=1.0, undercuts=0.0, minimum_inside_radius=1.0),
        )
        assert attached.scans_needed() == ("curvature", "draft", "thickness")

    def test_each_rule_carries_its_source_and_its_reason(self) -> None:
        [rule] = attach("welded", PLATE, _limits(minimum_wall=2.0)).rules
        assert rule.source == GUIDE
        assert "burns through" in rule.rationale
        assert rule.process == "welded"

    def test_the_rules_become_assertions_for_the_design_loop(self) -> None:
        attached = attach("printed", PLATE, _limits(minimum_wall=0.8, build_z=200.0))
        names = [a.name for a in attached.assertions()]
        assert names == ["printed.minimum_wall", "printed.build_z"]


class TestWhatAttachmentRefuses:
    def test_an_unknown_process(self) -> None:
        with pytest.raises(RuleError, match="forged"):
            attach("forged", PLATE, {})

    def test_a_limit_no_rule_reads(self) -> None:
        with pytest.raises(RuleError, match="no rule called minimum_draft"):
            attach("machined", PLATE, _limits(minimum_draft=1.0))

    def test_any_limit_on_sheet_metal_because_its_rules_live_on_the_fold_tree(self) -> None:
        with pytest.raises(RuleError, match="no rules on the solid"):
            attach("sheet", PLATE, _limits(minimum_wall=1.0))

    def test_a_limit_with_no_source(self) -> None:
        with pytest.raises(SourceError):
            Limit(value=1.0, source="")


class TestTheVerdictIsARedBuild:
    def _payload(self, **values: object) -> dict[str, object]:
        return dict(values)

    def test_a_part_too_big_for_the_machine_fails_by_name(self) -> None:
        attached = attach("machined", PLATE, _limits(travel_x=50.0, travel_y=50.0, travel_z=50.0))
        payload = {"bounding_box_mm": {"size": [60.0, 40.0, 20.0]}}
        # undercuts is unset, so even a clean part would not be ok; here x also fails.
        result = check(attached, payload)
        by_name = {r.rule.name: r for r in result.report.results}
        assert by_name["machined.travel_x"].outcome is Outcome.FAILED
        assert by_name["machined.travel_y"].outcome is Outcome.PASSED
        assert not result.ok

    def test_every_rule_satisfied_but_one_unset_is_still_not_ok(self) -> None:
        attached = attach("machined", PLATE, _limits(travel_x=100.0, travel_y=100.0, travel_z=100.0))
        result = check(attached, {"bounding_box_mm": {"size": [60.0, 40.0, 20.0]}})
        assert result.report.ok
        assert not result.ok
        assert "undercuts" in result.summary()

    def test_an_unscanned_wall_is_unmeasured_not_passed(self) -> None:
        attached = attach("welded", PLATE, _limits(minimum_wall=2.0))
        result = check(attached, {"volume_mm3": 1.0})
        assert result.report.unmeasured
        assert not result.ok
        assert result.to_dict()["scans_needed"] == ["thickness"]

    def test_a_sampled_wall_above_the_limit_passes_provisionally(self) -> None:
        attached = attach("welded", PLATE, _limits(minimum_wall=2.0))
        payload: dict[str, object] = {"minimum_wall_mm": 2.6}
        provenance.attach(payload, "minimum_wall_mm", provenance.approximated("ray cast"))
        result = check(attached, payload)
        assert result.ok
        assert not result.report.proven

    def test_nothing_attached_is_not_ok(self) -> None:
        result = check(attach("sheet", PLATE, {}), {"volume_mm3": 1.0})
        assert not result.ok
        assert "app/sheetmetal" in result.summary()
