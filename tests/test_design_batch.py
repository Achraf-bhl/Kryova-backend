"""A plan as one session, and as one script (E15 task 1).

The phase asks for "a `Plan` as one kernel session (OCCT); a CATScript executed
once (CATIA), never 10⁵ COM calls". The OCCT half is testable end to end here
because `execute_plan` takes an injected runner and the whole design package is
pure. **The CATIA half is emitted and never driven** — there is no seat on this
machine — so what is asserted is the shape of the script and the escaping, which
is where a generated script goes wrong silently rather than loudly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.design.batch import (
    BATCH_THRESHOLD,
    as_catscript,
    build,
    plan_for,
)
from app.design.compile import compile_spec
from app.design.spec import DesignSpec, FeatureSpec, expr, ref
from tests.test_design_compile import bracket


class Recorder:
    """A `CallRunner` that records rather than builds.

    The seam `app/design/execute.py` defines, which is what lets the whole of
    this be checked with no kernel anywhere near it.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.instances = 1

    def __call__(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((tool, dict(arguments)))
        return {"ok": True, "feature": f"F{len(self.calls)}"}


def wide(features: int) -> DesignSpec:
    """A spec with `features` independent sketches — enough to cross a threshold."""
    return DesignSpec.of(
        "Wide",
        features=[
            FeatureSpec(f"plate.s{index}", "catia_sketch_create", {"support": "XY"})
            for index in range(features)
        ],
    )


class TestTheBatchDecision:
    def test_a_small_plan_runs_interactively(self) -> None:
        """A batch threshold that swallowed a twelve-call part would take away
        the feedback the correction loop runs on."""
        decision = plan_for(bracket())

        assert not decision.batched
        assert "correction loop" in decision.reason

    def test_a_large_plan_is_batched_and_says_why(self) -> None:
        decision = plan_for(wide(BATCH_THRESHOLD + 5))

        assert decision.batched
        assert str(decision.calls) in decision.reason
        assert "measuring integrates" in decision.reason

    def test_the_threshold_is_a_parameter_not_a_constant_in_two_places(self) -> None:
        small = plan_for(bracket(), threshold=1)

        assert small.batched


class TestOneSession:
    def test_the_whole_plan_goes_through_one_runner(self) -> None:
        """One session is the point: the OCAF labels that make naming work have
        to persist between calls, and a runner per call would lose them."""
        recorder = Recorder()

        report, _ = build(bracket(), lambda _batch: recorder)

        assert report.failure is None
        assert len(recorder.calls) == len(compile_spec(bracket()).calls)

    def test_batching_does_not_change_what_gets_built(self) -> None:
        """Determinism (I5): the same spec compiles to the same geometry. A
        batch path that issued different calls would break it silently, and the
        plan digest is what pins that."""
        interactive = Recorder()
        batched = Recorder()

        one, _ = build(bracket(), lambda _b: interactive, threshold=10_000)
        two, _ = build(bracket(), lambda _b: batched, threshold=0)

        assert one.plan_digest == two.plan_digest
        assert interactive.calls == batched.calls

    def test_the_factory_is_told_whether_this_is_a_batch(self) -> None:
        """The decision has to reach the caller, because that is what lets it
        construct the runner at the right detail — deciding again outside would
        put the threshold in two places."""
        seen: list[bool] = []

        build(wide(BATCH_THRESHOLD + 2), lambda batch: (seen.append(batch.batched), Recorder())[1])

        assert seen == [True]


class TestTheCATScript:
    def test_it_is_one_script_with_one_entry_point(self) -> None:
        script = as_catscript(compile_spec(bracket()))

        assert script.count("Sub CATMain()") == 1
        assert script.count("End Sub") == 1

    def test_every_call_appears_once_in_plan_order(self) -> None:
        plan = compile_spec(bracket())

        script = as_catscript(plan)

        positions = [script.index(f"[{call.index}]") for call in plan.calls]
        assert positions == sorted(positions)
        assert len(positions) == len(plan.calls)

    def test_option_explicit_is_present(self) -> None:
        """CATScript is a VBScript dialect and an undeclared variable is a
        silent empty string — which in a geometry script is a pad of length
        zero rather than an error."""
        assert "Option Explicit" in as_catscript(compile_spec(bracket()))

    def test_a_quote_in_a_name_is_escaped_by_doubling(self) -> None:
        """Getting this wrong does not produce a syntax error in the useful
        case — it produces a *different string*, so a feature named `1" plate`
        silently becomes two arguments."""
        spec = DesignSpec.of(
            "Quoted",
            features=[
                FeatureSpec("plate.profile", "catia_sketch_create", {"support": '1" plate'})
            ],
        )

        script = as_catscript(compile_spec(spec))

        assert '"1"" plate"' in script

    def test_a_boolean_is_emitted_as_a_boolean_and_not_as_one(self) -> None:
        """`bool` is a subclass of `int`, so the numeric branch would emit `1` —
        which VBScript does treat as true, and which reads as a dimension in a
        script somebody has to debug."""
        spec = DesignSpec.of(
            "Flagged",
            features=[
                FeatureSpec("plate.profile", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "plate.body",
                    "catia_pad",
                    {"sketch": ref("plate.profile"), "length_mm": 8.0, "symmetric": True},
                ),
            ],
        )

        script = as_catscript(compile_spec(spec))

        assert '"symmetric", True' in script
        assert '"symmetric", 1' not in script

    def test_expressions_are_already_resolved_in_the_script(self) -> None:
        """A plan's arguments are resolved before it exists. A script that
        recomputed them would be a second compiler with its own opinion about
        `wall_mm * 2`."""
        spec = DesignSpec.of(
            "Derived",
            parameters=[_mm("thick_mm", 8.0)],
            features=[
                FeatureSpec("plate.profile", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "plate.body",
                    "catia_pad",
                    {"sketch": ref("plate.profile"), "length_mm": expr("thick_mm * 2")},
                ),
            ],
        )

        script = as_catscript(compile_spec(spec))

        assert "16" in script
        assert "thick_mm * 2" not in script

    def test_the_dispatcher_name_is_configurable_and_used_everywhere(self) -> None:
        script = as_catscript(compile_spec(bracket()), dispatcher="MyDispatch")

        assert "KryovaDispatch" not in script
        assert script.count("MyDispatch(") == len(compile_spec(bracket()).calls)

    def test_a_list_argument_is_not_split_on_a_comma(self) -> None:
        """A feature name may contain a comma, so a comma-delimited list would
        split one name into two references.

        Tested against the literal writer directly rather than through a spec:
        the compiler validates arguments against the operation registry, so
        exercising this through a feature would need an operation that happens
        to take a list — and the test would then break when that operation's
        signature changed, for a reason that has nothing to do with escaping.
        """
        from app.design.batch import _vb_literal

        assert _vb_literal(["a,b", "c"]) == '"a,b\x1fc"'

    def test_a_dictionary_argument_keeps_its_keys_with_their_values(self) -> None:
        from app.design.batch import _vb_literal

        assert _vb_literal({"b": 2, "a": 1}) == '"a\x1e1\x1fb\x1e2"'

    def test_nothing_is_emitted_as_empty_rather_than_as_the_word_none(self) -> None:
        # `"None"` is a four-character string in VBScript, and a pad given a
        # length of `"None"` is a pad of length zero.
        from app.design.batch import _vb_literal

        assert _vb_literal(None) == "Empty"

    def test_the_header_says_not_to_edit_it(self) -> None:
        # The design is the spec. An edit here is lost on the next build, and
        # somebody discovering that the hard way loses an afternoon.
        assert "Do not edit by hand" in as_catscript(compile_spec(bracket()))


def _mm(name: str, value: float):
    from app.design.params import Parameter, Unit

    return Parameter(name, Unit.MM, value=value)
