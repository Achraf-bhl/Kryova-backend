"""Running a compiled plan.

Each test is named after the failure it prevents rather than the function it
calls, matching the rest of the design suite.

* **The late-bound name resolves from what actually ran**, not from a predicted
  `Pad.1`. That prediction is the positional fragility Layer B exists to remove,
  so the executor reading it back off the result is the whole contract.
* **A failure stops the run and names the feature.** A build is a stack; going
  on past a failed pad reports errors about geometry that was never made and
  buries the one that mattered.
* **What was built is reported, not what was intended.** A diagnosis and a
  measurement both ask about the part that exists.

Offline: no database fixture, no CATIA, no gmsh.
"""

from collections.abc import Mapping
from typing import Any

from app.design.compile import Created, compile_spec
from app.design.execute import BuildReport, execute_plan
from app.design.spec import DesignSpec, FeatureSpec, ref
from tests.test_design_compile import bracket


class Recorder:
    """A runner that records what it was asked to do and answers plausibly.

    Mimics the daemon's `_mutation_result`: every mutating tool returns the
    element's CATIA name alongside the post-state.
    """

    def __init__(self, fail_at: str | None = None, message: str = "CATIA refused it") -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        self.fail_at = fail_at
        self.message = message
        self._counter = 0

    def __call__(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((tool, dict(arguments)))
        if tool == self.fail_at:
            raise RuntimeError(self.message)
        self._counter += 1
        return {
            "feature": f"{tool.removeprefix('catia_').title()}.{self._counter}",
            "mass_kg": 0.6,
            "bounding_box_mm": {"size": [120.0, 80.0, 8.0]},
        }

    @property
    def tools(self) -> list[str]:
        return [tool for tool, _ in self.calls]


class TestALateBoundNameComesFromTheRun:
    def test_the_rename_is_given_what_catia_actually_called_the_feature(self) -> None:
        """The one value a plan cannot know until it runs.

        `catia_pad` takes no `name`, so the compiler follows it with a rename
        whose `feature` argument is `Created("plate.body")`. If the executor
        substituted a predicted name here, every design would break the first
        time CATIA numbered a feature differently than expected — which is
        exactly what happens on a part that already has a `Pad.1`.
        """
        recorder = Recorder()
        plan = compile_spec(bracket())

        report = execute_plan(plan, recorder)

        assert report.ok
        renames = [
            arguments
            for tool, arguments in recorder.calls
            if tool == "catia_feature_rename"
        ]
        assert renames, "the plan should rename the pad, which cannot be named on creation"
        for arguments in renames:
            assert not isinstance(arguments["feature"], Created)
            assert isinstance(arguments["feature"], str)
        # The pad reported Pad.N; that is what the rename must have been handed.
        assert renames[0]["feature"] == report.created["plate.body"]

    def test_a_creating_call_that_reports_no_name_fails_before_the_call(self) -> None:
        """A rename with nothing to rename must not be sent hopefully.

        Distinguished from a seat refusal because retrying cannot fix it: the
        fault is in the tool that failed to report its feature, not in the
        design.
        """

        def silent(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            return {}  # no "feature" key

        report = execute_plan(compile_spec(bracket()), silent)

        assert not report.ok
        assert report.failure is not None
        assert report.failure.before_the_call
        assert report.failure.tool == "catia_feature_rename"
        assert "late-bound" in report.failure.message

    def test_the_creating_calls_name_is_kept_not_the_renames(self) -> None:
        """Both calls carry the same semantic name; the first one is the answer.

        `created` means "what did CATIA call it when it was made". The rename
        returns the new name, so recording the last write would put the wrong
        answer under a key whose only purpose is the old one.
        """
        recorder = Recorder()
        plan = compile_spec(bracket())
        report = execute_plan(plan, recorder)

        pad_calls = [tool for tool, _ in recorder.calls if tool == "catia_pad"]
        assert pad_calls, "fixture should contain a pad"
        assert report.created["plate.body"].startswith("Pad.")


class TestAFailureStopsAndNamesTheFeature:
    def test_the_run_stops_at_the_first_failure(self) -> None:
        recorder = Recorder(fail_at="catia_pad")
        plan = compile_spec(bracket())

        report = execute_plan(plan, recorder)

        assert not report.ok
        assert "catia_fillet" not in recorder.tools, (
            "nothing after the failed pad should have been attempted"
        )

    def test_the_failure_is_reported_by_semantic_name(self) -> None:
        """`plate.body`, not `Pad.2`. That is the handle the author has on it."""
        report = execute_plan(compile_spec(bracket()), Recorder(fail_at="catia_pad"))

        assert report.failure is not None
        assert report.failure.feature == "plate.body"
        assert report.failure.tool == "catia_pad"
        assert "CATIA refused it" in report.failure.message
        assert "plate.body" in str(report.failure)

    def test_what_completed_before_the_failure_is_kept(self) -> None:
        """A diagnosis turns on what was built, so a failed run is not empty."""
        report = execute_plan(compile_spec(bracket()), Recorder(fail_at="catia_pad"))

        assert not report.ok
        assert len(report) > 0
        assert "plate.profile" in report.features_built()
        assert "plate.body" not in report.features_built()

    def test_any_exception_is_an_outcome_not_a_crash(self) -> None:
        """A seat refusal, a timeout and a dead socket all mean the same thing here."""

        def explode(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            raise TimeoutError("the workstation stopped answering")

        report = execute_plan(compile_spec(bracket()), explode)

        assert not report.ok
        assert report.failure is not None
        assert "stopped answering" in report.failure.message

    def test_an_exception_with_no_message_still_names_something(self) -> None:
        def explode(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            raise RuntimeError

        report = execute_plan(compile_spec(bracket()), explode)

        assert report.failure is not None
        assert report.failure.message == "RuntimeError"


class TestTheReport:
    def test_a_complete_run_is_truthy_and_a_partial_one_is_not(self) -> None:
        assert execute_plan(compile_spec(bracket()), Recorder())
        assert not execute_plan(compile_spec(bracket()), Recorder(fail_at="catia_pad"))

    def test_every_call_in_the_plan_is_made(self) -> None:
        plan = compile_spec(bracket())
        recorder = Recorder()

        report = execute_plan(plan, recorder)

        assert len(report) == len(plan)
        assert recorder.tools == list(plan.tools())

    def test_suppressed_features_are_carried_into_the_report(self) -> None:
        """"Where is the pocket?" is answerable from the report alone."""
        report = execute_plan(compile_spec(bracket()), Recorder())

        assert "plate.window" in report.suppressed

    def test_the_last_result_is_the_freshest_post_state(self) -> None:
        report = execute_plan(compile_spec(bracket()), Recorder())

        assert report.last_result()["mass_kg"] == 0.6

    def test_a_report_with_nothing_in_it_has_no_last_result(self) -> None:
        empty = BuildReport(design="D", plan_digest="x")

        assert empty.last_result() == {}
        assert empty.ok

    def test_stop_after_runs_only_a_prefix(self) -> None:
        """What a partial rebuild needs: replay up to the first thing that differs."""
        recorder = Recorder()
        plan = compile_spec(bracket())

        report = execute_plan(plan, recorder, stop_after=3)

        assert len(report) == 3
        assert report.ok
        assert recorder.tools == list(plan.tools()[:3])

    def test_the_report_serialises_to_something_readable(self) -> None:
        report = execute_plan(compile_spec(bracket()), Recorder(fail_at="catia_pad"))
        data = report.to_dict()

        assert data["ok"] is False
        assert data["failure"]["feature"] == "plate.body"
        assert data["design"] == "Bracket"


class TestTheRunnerSeam:
    def test_the_executor_sends_resolved_literals_only(self) -> None:
        """Nothing symbolic reaches the runner: `bind` runs first, every time."""
        recorder = Recorder()

        execute_plan(compile_spec(bracket()), recorder)

        for _tool, arguments in recorder.calls:
            for value in arguments.values():
                assert not isinstance(value, Created)

    def test_a_design_with_no_late_binding_still_runs(self) -> None:
        """Not every design contains a feature CATIA refuses to name."""
        design = DesignSpec.of(
            "Simple",
            features=[
                FeatureSpec("s", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "r",
                    "catia_sketch_rectangle",
                    {"sketch": ref("s"), "width_mm": 10.0, "height_mm": 10.0},
                ),
            ],
        )
        recorder = Recorder()

        report = execute_plan(compile_spec(design), recorder)

        assert report.ok
        assert "catia_feature_rename" not in recorder.tools


def test_a_plan_can_be_run_repeatedly_without_state_leaking() -> None:
    """Two runs of one plan must not see each other's `created` map."""
    plan = compile_spec(bracket())
    first = execute_plan(plan, Recorder())
    second = execute_plan(plan, Recorder())

    assert first.created == second.created
    assert first.plan_digest == second.plan_digest
