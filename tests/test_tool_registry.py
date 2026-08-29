"""The agent's tool vocabulary, checked against everything that names a tool.

Three separate things spell tool names as string literals: `ToolBox` (which
registers them), `app/ai/agent.py` (which labels and summarises them for the
UI), and route handlers that reach into the toolbox so a button and the
assistant share one implementation. Nothing made them agree, and the merge that
brought the WebSocket bridge alongside the direct-COM bridge proved why: the
block that registered `open_in_catia` and `sync_geometry_from_catia` landed
*after* `_build`'s `return`, so it never ran, and `POST /catia/projects/{id}/sync`
answered every request with 503 "There is no tool called
'sync_geometry_from_catia'". Every test in the suite passed.

These tests are offline on purpose -- they build a `ToolBox` with no session
because `_build` never touches one, so a broken vocabulary is caught in the
sub-second loop rather than four minutes into the database suite.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, cast

import pytest

from app.ai import agent
from app.ai.tools import ToolBox
from app.core.config import BASE_DIR

APP = BASE_DIR / "app"

#: Statements that end a block. Anything the parser puts after one of these, in
#: the same block, is unreachable no matter what the conditions above it do.
TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


@pytest.fixture(scope="module")
def registered() -> set[str]:
    """Every tool name a `ToolBox` actually answers to.

    `db` and `user` are never read while the vocabulary is being built -- the
    handlers close over `self` and only dereference them when called -- so this
    needs no database and no fixture that opens one.
    """
    box = ToolBox(db=cast(Any, None), user=cast(Any, None))
    return set(box.labels())


def _source_files() -> list[Path]:
    return sorted(APP.rglob("*.py"))


def _rel(path: Path) -> str:
    return path.relative_to(BASE_DIR).as_posix()


class TestNoUnreachableCode:
    """Code after a `return` is code no test can ever fail.

    A plain `F821` from ruff caught the undefined name inside the dead block,
    but only because that block happened to reference a variable that no longer
    existed. A dead block that is internally consistent raises nothing at all,
    which is the case worth catching here.
    """

    def test_no_statement_follows_a_terminator_in_the_same_block(self) -> None:
        dead: list[str] = []
        for path in _source_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                for field in ("body", "orelse", "finalbody"):
                    block = getattr(node, field, None)
                    if not isinstance(block, list):
                        continue
                    for index, statement in enumerate(block[:-1]):
                        if isinstance(statement, TERMINATORS):
                            follower = block[index + 1]
                            dead.append(
                                f"{_rel(path)}:{follower.lineno} is unreachable -- "
                                f"{type(statement).__name__.lower()} on line {statement.lineno}"
                            )
                            break
        assert not dead, "unreachable code:\n  " + "\n  ".join(dead)


class TestEveryToolNameInvokedExists:
    """A route that calls a tool by a name the toolbox lost is a 503 generator.

    `POST /catia/projects/{id}/sync` shares its implementation with the agent
    tool deliberately -- "the button and the assistant cannot drift apart" is
    the stated reason in its docstring. Nothing enforced that until here.
    """

    @staticmethod
    def _invoked_names() -> list[tuple[str, int, str]]:
        """Every `<something>.call("literal", ...)` site under `app/`."""
        found: list[tuple[str, int, str]] = []
        for path in _source_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr != "call":
                    continue
                if not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.append((_rel(path), node.lineno, first.value))
        return found

    def test_the_sync_route_still_has_a_tool_to_call(self, registered: set[str]) -> None:
        # Named explicitly as well as covered by the sweep below: this is the
        # endpoint the merge broke, and a bare sweep would not say so.
        invoked = {name for _, _, name in self._invoked_names()}
        if "sync_geometry_from_catia" not in invoked:
            pytest.skip("the sync route no longer calls a tool by name")
        assert "sync_geometry_from_catia" in registered, (
            "POST /catia/projects/{project_id}/sync calls "
            "toolbox.call('sync_geometry_from_catia'), which ToolBox does not register. "
            "Either register the tool or change the route to use the bridge tools."
        )

    def test_every_literal_tool_call_resolves(self, registered: set[str]) -> None:
        unknown = [
            f"{path}:{line} calls {name!r}"
            for path, line, name in self._invoked_names()
            if name not in registered
        ]
        assert not unknown, (
            "tool names invoked but never registered:\n  " + "\n  ".join(unknown)
        )


class TestAgentVocabularyMatchesTheToolbox:
    """The UI's labels and summaries are written against names, not objects.

    A label for a tool that does not exist is dead weight that reads as a
    shipped capability; a tool with no label renders as its raw snake_case name
    in the step list, which is the thing `TOOL_LABELS` exists to prevent.
    """

    @staticmethod
    def _summarised_names() -> set[str]:
        """Names compared against in `agent.summarise_step`'s `tool == "..."`."""
        source = (APP / "ai" / "agent.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "summarise_step"
        )
        names: set[str] = set()
        for node in ast.walk(function):
            if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
                continue
            if node.left.id != "tool":
                continue
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                    if isinstance(comparator.value, str):
                        names.add(comparator.value)
        return names

    def test_every_label_names_a_real_tool(self, registered: set[str]) -> None:
        stale = sorted(set(agent.TOOL_LABELS) - registered)
        assert not stale, (
            f"TOOL_LABELS entries for tools that do not exist: {stale}. "
            "They label a capability the agent cannot offer."
        )

    def test_every_summary_branch_names_a_real_tool(self, registered: set[str]) -> None:
        stale = sorted(self._summarised_names() - registered)
        assert not stale, (
            f"summarise_step has branches for tools that do not exist: {stale}."
        )

    def test_no_registered_tool_renders_as_raw_snake_case(self, registered: set[str]) -> None:
        # `tool_label` falls back to replacing underscores with spaces, which is
        # the "we forgot this one" rendering rather than a written label.
        from app.ai.tools import tool_label

        unlabelled = sorted(
            name
            for name in registered
            if not name.startswith("catia_") and tool_label(name) == name.replace("_", " ")
        )
        assert not unlabelled, f"tools with no written label: {unlabelled}"


class TestCatiaVocabularyIsOneList:
    """`CATIA_TOOL_SPECS` is the single source; nothing may hard-code beside it."""

    def test_every_catia_tool_comes_from_the_spec_table(self, registered: set[str]) -> None:
        from app.catia.tool_specs import CATIA_TOOL_SPECS

        specs = {spec.name for spec in CATIA_TOOL_SPECS}
        registered_catia = {name for name in registered if name.startswith("catia_")}
        assert registered_catia == specs, (
            "the CATIA tools the agent exposes have drifted from the spec table: "
            f"only in ToolBox {sorted(registered_catia - specs)}, "
            f"only in CATIA_TOOL_SPECS {sorted(specs - registered_catia)}"
        )

    def test_the_document_exemptions_name_real_tools(self) -> None:
        from app.ai.tools import CATIA_NO_DOCUMENT_REQUIRED
        from app.catia.tool_specs import CATIA_TOOL_SPECS

        specs = {spec.name for spec in CATIA_TOOL_SPECS}
        stale = sorted(CATIA_NO_DOCUMENT_REQUIRED - specs)
        assert not stale, (
            f"CATIA_NO_DOCUMENT_REQUIRED exempts tools that do not exist: {stale}"
        )
