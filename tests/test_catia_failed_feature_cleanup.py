"""A refused CATIA operation must take its wreckage out of the tree with it.

`ShapeFactory.AddNew...` puts the feature in the specification tree immediately;
`Part.Update()` is what evaluates it. So a feature that cannot build is already
*in the part* when the update fails, sitting in an error state -- and CATIA then
fails every later `Update()` on that body, whatever it is for. One refused call
turns a working part into a dead one.

This has now been measured three times, on three different operations, and
twice it was fixed only at the call site that happened to be under the
microscope:

* a 200 mm fillet was refused cleanly, and a perfectly reasonable 1 mm fillet on
  the same part then failed with a bare COM error -- the fix, and the
  `_discard_failed_feature` docstring, came from that one;
* ladder prompt H1, 2026-09-06: a refused `catia_hole` left its `Poche.1`
  behind, the part measured 0 mm3 with a good pad still in it, and the next
  hole was refused for having "no solid body";
* ladder prompt H2, the same day: `pad` had no handler at all. A pad from a
  sketch holding a rectangle *and* two circles raised the raw French
  `La methode Update a echoue`, left `Extrusion.1` in the tree, and every call
  after it -- sketch_close, a second pad, a pocket -- returned that same
  error. The screenshot shows `Corps principal` carrying an update badge with
  nothing drawn. The run never recovered.

Seven more call sites had the same hole after H1 was fixed. That is the real
finding: a rule you have to remember at eleven call sites is a rule that gets
forgotten. So `_update_or_discard` is now the only sanctioned way to update
after creating a feature, and the first test here is the structural one that
keeps it that way -- it fails on a *new* operation written the old way, which
is the case none of the three incidents were caught by.

No CATIA, no seat: the structural test reads the source, and the behavioural
one drives `_update_or_discard` against a fake part that fails on demand.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parent.parent / "scripts" / "catia_bridge" / "catia_com.py"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.catia_com import CatiaCom  # noqa: E402


def _method_nodes() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    klass = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CatiaCom"
    )
    return {
        node.name: node
        for node in klass.body
        if isinstance(node, ast.FunctionDef)
    }


def _calls_in(node: ast.AST) -> set[str]:
    """Every attribute name called anywhere inside `node`."""
    return {
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }


def _update_calls(node: ast.AST) -> list[ast.Call]:
    """Every `....Update()` call anywhere inside `node`."""
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "Update"
    ]


def _creation_calls(node: ast.AST) -> list[ast.Call]:
    """Every `ShapeFactory.AddNew...` call anywhere inside `node`."""
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr.startswith("AddNew")
    ]


def _creates_a_feature(node: ast.FunctionDef) -> bool:
    """Does this method put something new in the specification tree?

    Keyed on the `AddNew...` family, which is exactly the set of calls that
    have the property this file is about: the tree changes before anything has
    been evaluated.
    """
    return any(name.startswith("AddNew") for name in _calls_in(node))


#: Creates a feature but is itself the cleanup, or hands the update to a caller.
_EXEMPT = {
    "_discard_failed_feature",
    "_update_or_discard",
    "_discard",
    # Builds throwaway offset planes to find a bounding box and deletes them
    # itself in a `finally`; it never leaves a feature behind to clean up.
    "_bounding_box",
}


class TestEveryFeatureCreatorCleansUp:
    """The structural invariant. This is the test that generalises."""

    def test_the_scan_finds_the_operations_we_expect(self) -> None:
        """If this stops matching reality the invariant below is vacuous.

        Named explicitly rather than counted: a rename that dropped `pad` out
        of the scan would leave the real test passing over nothing.
        """
        creators = {n for n, node in _method_nodes().items() if _creates_a_feature(node)}
        for expected in ("pad", "pocket", "hole", "fillet", "chamfer", "shaft", "groove"):
            assert expected in creators, f"{expected} no longer looks like a feature creator"

    def test_no_update_runs_without_a_handler_that_cleans_up(self) -> None:
        """Every `Update()` -- not merely somewhere in the method.

        The first version of this test asked only whether the method mentioned
        `_discard_failed_feature` anywhere, and it **passed with `pad` restored
        to the exact bare `self._part().Update()` that broke H2** -- because
        `pad` also wraps `AddNewPad` in a handler that cleans up, and that was
        enough to satisfy it. A guard that cannot be made to fail by
        reintroducing the defect is not guarding anything, so it is asked per
        call site instead: an `Update()` that is not lexically inside a `try`
        whose handlers clean up is an `Update()` that can strand a feature.

        `_update_or_discard` satisfies it by containing no `Update()` of its
        own at the call site -- which is the point of routing through it.
        """
        offenders: list[str] = []
        for name, node in _method_nodes().items():
            if name in _EXEMPT or not _creates_a_feature(node):
                continue
            guarded = {
                id(call)
                for block in ast.walk(node)
                if isinstance(block, ast.Try)
                and any("_discard_failed_feature" in _calls_in(h) for h in block.handlers)
                for statement in block.body
                for call in _update_calls(statement)
            }
            for call in _update_calls(node):
                if id(call) not in guarded:
                    offenders.append(f"{name}() line {call.lineno}")

        assert not offenders, (
            "These call Update() while a newly created feature could still be "
            f"stranded by it: {offenders}. Go through "
            "self._update_or_discard(feature, advice) -- a feature left in an error "
            "state makes every later Update on the body fail, so one refused call "
            "kills the part for the rest of the session."
        )

    def test_no_feature_is_created_outside_a_handler(self) -> None:
        """The other half, and the one H3 hit. `catia_com.py` only.

        A feature that fails *at creation* strands nothing -- `AddNew...` never
        returned -- so the cleanup rule above does not apply and its test passes
        happily. What it does instead is escape as a raw COM error:

            com_error while running catia_pocket: (-2147352567, "Une exception
            s'est produite.", (0, 'CATIAShapeFactory', 'La methode AddNewPocket
            a echoue', ...))

        which names no cause and offers no remedy, so the agent stopped using
        the modelling tools and started driving CATIA's menus by hand. Both
        halves of every feature operation have to be answerable.
        """
        offenders: list[str] = []
        for name, node in _method_nodes().items():
            if name in _EXEMPT:
                continue
            guarded = {
                id(call)
                for block in ast.walk(node)
                if isinstance(block, ast.Try)
                for statement in block.body
                for call in _creation_calls(statement)
            }
            for call in _creation_calls(node):
                if id(call) not in guarded:
                    offenders.append(f"{name}() line {call.lineno}: {call.func.attr}")  # type: ignore[union-attr]

        assert not offenders, (
            f"These create a feature outside any handler: {offenders}. A COM failure "
            "there reaches the agent as a raw com_error with no cause and no remedy. "
            "Wrap it and say what to try instead."
        )


class _FakePart:
    """A part whose `Update` fails once, the way a bad profile makes it."""

    def __init__(self, *, fail_times: int = 1) -> None:
        self.fail_times = fail_times
        self.updates = 0

    def Update(self) -> None:  # noqa: N802 - COM spelling
        self.updates += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError(
                "(-2147352567, 'Une exception s'est produite.', "
                "(0, 'CATIAPart', 'La methode Update a echoue', None, 0, -2147467259), None)"
            )


class _FakeSelection:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.deleted = False

    def Clear(self) -> None:  # noqa: N802
        self.added.clear()

    def Add(self, shape: Any) -> None:  # noqa: N802
        self.added.append(shape)

    def Delete(self) -> None:  # noqa: N802
        self.deleted = True


class _FakeDocument:
    def __init__(self) -> None:
        self.Selection = _FakeSelection()  # noqa: N815 - COM spelling


class _Shape:
    Name = "Extrusion.1"


@pytest.fixture
def com(monkeypatch: pytest.MonkeyPatch) -> CatiaCom:
    """A `CatiaCom` with no COM behind it.

    Built without `__init__` on purpose: the real one connects to CATIA, and
    the behaviour under test does not need a connection -- only `_part` and
    `_document`, both of which are stubbed per test.
    """
    return CatiaCom.__new__(CatiaCom)


class TestUpdateOrDiscard:
    def test_a_clean_update_leaves_the_feature_alone(self, com: CatiaCom) -> None:
        part, document = _FakePart(fail_times=0), _FakeDocument()
        com._part = lambda: part  # type: ignore[method-assign]
        com._document = lambda: document  # type: ignore[method-assign]

        com._update_or_discard(_Shape(), "should not be seen.")

        assert part.updates == 1
        assert not document.Selection.deleted

    def test_a_failed_update_removes_the_feature(self, com: CatiaCom) -> None:
        """The whole point: the tree goes back to what it was.

        The second update -- the one inside the cleanup -- is what actually
        clears the body's error state, so it is asserted too: deleting the
        feature without updating leaves the part exactly as unusable.
        """
        part, document = _FakePart(fail_times=1), _FakeDocument()
        com._part = lambda: part  # type: ignore[method-assign]
        com._document = lambda: document  # type: ignore[method-assign]
        shape = _Shape()

        with pytest.raises(CatiaOperationError):
            com._update_or_discard(shape, "CATIA could not extrude Sketch.1.")

        assert document.Selection.deleted, "the broken feature was left in the tree"
        assert document.Selection.added == [shape], "something other than the feature was deleted"
        assert part.updates == 2, "the body's error state was never cleared"

    def test_the_refusal_says_the_part_is_still_usable(self, com: CatiaCom) -> None:
        part, document = _FakePart(fail_times=1), _FakeDocument()
        com._part = lambda: part  # type: ignore[method-assign]
        com._document = lambda: document  # type: ignore[method-assign]

        with pytest.raises(CatiaOperationError) as caught:
            com._update_or_discard(_Shape(), "CATIA could not extrude Sketch.1.")
        message = str(caught.value)

        # The caller's advice survives.
        assert "CATIA could not extrude Sketch.1." in message
        # CATIA's own words survive too -- they are the only thing that
        # distinguishes one Update failure from another.
        assert "La methode Update a echoue" in message
        # And the sentence that stops the agent starting the part again.
        assert "still buildable" in message

    def test_cleanup_failure_never_replaces_the_real_error(self, com: CatiaCom) -> None:
        """A cleanup that raises would hide the message the agent needs.

        `_discard_failed_feature` swallows its own failures for this reason;
        asserted here because the swallow looks like something to tidy up.
        """
        part = _FakePart(fail_times=1)
        com._part = lambda: part  # type: ignore[method-assign]

        def _broken_document() -> Any:
            raise RuntimeError("COM is gone")

        com._document = _broken_document  # type: ignore[method-assign]

        with pytest.raises(CatiaOperationError, match="could not extrude"):
            com._update_or_discard(_Shape(), "CATIA could not extrude Sketch.1.")


def test_the_mixins_are_covered_by_the_generic_translator() -> None:
    """The blind spot in the tests above, recorded rather than papered over.

    Everything above reads `catia_com.py` and its `CatiaCom` class. The bridge
    is much larger than that: `scripts/catia_bridge/com/` holds Part Design,
    surfaces, wireframe, reference geometry and assembly, and a scan on
    2026-09-06 found **83** `AddNew...` calls there with no handler of their
    own. `catia_fillet_edges` was one of them, and H3 hit it.

    Wrapping 83 call sites individually was rejected: a rule enforced at 83
    places is the rule that was already forgotten at eleven. They are covered
    instead at the single point every unhandled exception passes through --
    `session._handle_call` -> `com_errors.explain` -- which turns the raw COM
    tuple into the failed method plus advice for its family.

    So this asserts the funnel, not the call sites: that the daemon still routes
    unhandled failures through the translator. Take that out and 83 operations
    silently go back to answering with a COM tuple.
    """
    session_source = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "catia_bridge"
        / "session.py"
    ).read_text(encoding="utf-8")
    assert "com_errors.explain(tool, exc)" in session_source, (
        "the daemon no longer translates unhandled COM failures; every operation "
        "in scripts/catia_bridge/com/ answers with a raw COM tuple again"
    )


def test_the_scan_would_notice_a_new_unguarded_operation_in_catia_com() -> None:
    """Sanity: the file the structural tests read is the file that has them.

    A move of `pad` or `pocket` into a mixin would make those tests pass over
    almost nothing without failing, which is the quiet way a guard dies.
    """
    creators = {n for n, node in _method_nodes().items() if _creates_a_feature(node)}
    assert len(creators) >= 8, f"only {len(creators)} feature creators left in catia_com.py"
