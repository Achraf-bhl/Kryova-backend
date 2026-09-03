"""Every declared tool has a COM method, and every COM method is declared.

Neither direction is checkable by running the code: `CatiaCom` only executes on
Windows against a licensed seat, so a tool added to the registry with no
implementation behind it fails for the first engineer to try it rather than in
CI. These checks are structural — they read the class, they do not call it — so
they run anywhere and catch the drift on the commit that introduces it.

The reverse direction matters just as much. A method left on the backend after
its operation was renamed is dead code that looks live, and `implemented_tools`
reports capability by looking for exactly these names, so a stale one would have
the daemon advertising a tool the server no longer knows how to send.
"""

from __future__ import annotations

import inspect

from app.catia.ops.registry import OPERATIONS, TOOL_METHODS
from scripts.catia_bridge.backend import CORE_METHODS, CatiaBackend
from scripts.catia_bridge.catia_com import CatiaCom
from scripts.catia_bridge.mock_catia import MockCatia


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name, value in inspect.getmembers(cls, callable)
        if not name.startswith("_") and not isinstance(value, type)
    }


def test_every_operation_has_a_com_implementation() -> None:
    # The whole point of the generated registry is that adding a tool is one
    # edit. This is the check that the edit is not *only* one edit -- the
    # daemon still has to be able to run what the model is offered.
    missing = sorted(
        f"{op.name} -> {TOOL_METHODS[op.name]}"
        for op in OPERATIONS
        if not op.server_only and TOOL_METHODS[op.name] not in _public_methods(CatiaCom)
    )
    assert missing == [], (
        f"{len(missing)} operations are declared with no COM method behind them. "
        "Implement them in the matching scripts/catia_bridge/com/ mixin, or mark "
        f"the operation server_only: {missing}"
    )


def test_no_com_method_is_left_behind_after_a_rename() -> None:
    # Anything the ABC itself declares is lifecycle rather than a tool --
    # `ensure_connected`, `close`. Taken from the base class rather than listed
    # here, so adding a hook does not also mean remembering to edit this test.
    lifecycle = _public_methods(CatiaBackend)
    declared = set(TOOL_METHODS.values()) | CORE_METHODS | lifecycle
    stale = sorted(_public_methods(CatiaCom) - declared - _public_methods(object))
    assert stale == [], (
        f"These backend methods match no operation: {stale}. Either they are dead "
        "code from a rename, or a helper that should be named with a leading "
        "underscore so it is not mistaken for a tool."
    )


def test_the_mock_reports_the_tools_it_actually_has() -> None:
    """The mock is allowed to cover less than COM — but not to lie about it.

    `MockCatia` models a box, not a modelling kernel, so it genuinely cannot
    implement every surfacing tool. That is fine and handled: capability travels
    in the `hello` frame and the server offers the agent only what the connected
    bridge reports. What would not be fine is the mock defining a method that
    does nothing, because that is indistinguishable from a working tool right up
    until someone trusts its answer.
    """
    mock_tools = _public_methods(MockCatia)
    for name in mock_tools & set(TOOL_METHODS.values()):
        method = getattr(MockCatia, name)
        source = inspect.getsource(method)
        body = source.split("\n", 1)[1] if "\n" in source else ""
        assert "pass" != body.strip(), f"MockCatia.{name} is an empty stub"


def test_core_methods_are_implemented_by_both_backends() -> None:
    # `CORE_METHODS` comes off the ABC's own __abstractmethods__, so this is
    # really a check that neither backend is abstract by accident.
    for backend in (CatiaCom, MockCatia):
        assert not getattr(backend, "__abstractmethods__", frozenset()), (
            f"{backend.__name__} is still abstract: "
            f"{sorted(backend.__abstractmethods__)}"
        )
    assert CORE_METHODS <= _public_methods(CatiaBackend)
