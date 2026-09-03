"""The registry declares arguments; the backend method has to be able to take them.

`test_com_backend_covers_the_registry.py` checks that every operation has a
method of the right *name* behind it. That is half the contract, and the half
that was already being checked when this failed live:

    CATIA: sketch polygon
    — CATIA refused catia_sketch_polygon: TypeError while running
      catia_sketch_polygon: CatiaCom.sketch_polygon() got an unexpected
      keyword argument 'sketch'

The name matched. The signature did not. Two separate faults produced that, and
both are checked here.

**Shadowing.** `CatiaCom` inherits its operations from the mixins in
`scripts/catia_bridge/com/`, and a method left behind on `CatiaCom` itself from
before the registry rewrite *wins the MRO* over the mixin that supersedes it —
silently, because both exist and both are callable. Three sketch operations were
being served by their pre-registry implementations. This is the same fault as
the assembly tools reconciled in `03d238e`, which is twice, which is why it gets
a test rather than a fix.

**Narrowing.** The registry rewrite widened sixteen operations with new optional
arguments that were never implemented in COM — `catia_pad` gained `limit`,
`up_to`, `thin` and the rest. The model is offered them, the daemon's generated
table accepts them, and the method raises `TypeError`. That gap is real and
cannot be closed from Linux, so it is *recorded* below rather than asserted
away: `KNOWN_NARROWER` is the list, it is checked to be exact in both
directions, and it is meant to shrink.

Structural throughout — these read signatures, they never call CATIA — so they
run anywhere, and they catch the drift on the commit that introduces it rather
than on the first engineer to hit it.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.catia.ops.registry import OPERATIONS, OPERATIONS_BY_NAME
from scripts.catia_bridge.backend import unimplemented_options
from scripts.catia_bridge.catia_com import CatiaCom
from scripts.catia_bridge.com import (
    AssemblyMixin,
    AssemblyReviewMixin,
    DraftingMixin,
    InfrastructureMixin,
    InspectionMixin,
    KnowledgeMixin,
    PartDesignMixin,
    ReferenceMixin,
    SketchEditMixin,
    SketcherMixin,
    SurfacesMixin,
    WireframeMixin,
)

MIXINS = (
    SketcherMixin,
    SketchEditMixin,
    ReferenceMixin,
    PartDesignMixin,
    SurfacesMixin,
    WireframeMixin,
    AssemblyMixin,
    AssemblyReviewMixin,
    DraftingMixin,
    InfrastructureMixin,
    KnowledgeMixin,
    InspectionMixin,
)

#: Operations whose COM method is narrower than the schema the model is offered,
#: with the arguments it cannot take. Every one is an option the registry
#: rewrite added and COM never gained.
#:
#: **This is a debt list, not a specification.** Each entry means the model can
#: ask for something it will be refused. The refusal is at least legible now
#: (`unimplemented_options`), but the honest fix is to implement the option in
#: the matching `scripts/catia_bridge/com/` mixin and delete the line — which
#: needs a licensed seat to verify, so it is blocked behind roadmap A1.
#:
#: Asserted **exactly**, in both directions: a new gap fails, and closing one
#: without deleting its line here fails too. Otherwise the list rots into a
#: blanket exemption, which is how the original drift survived a test suite.
KNOWN_NARROWER: dict[str, list[str]] = {
    "catia_capture_view": ["fit", "mode"],
    "catia_chamfer": ["second_length_mm"],
    "catia_delete_feature": ["with_children"],
    "catia_fillet": ["propagation"],
    "catia_groove": ["axis", "second_angle_deg"],
    "catia_list_features": ["body", "include_sketches", "kind"],
    "catia_list_parameters": ["filter", "include_dimensions"],
    "catia_measure": ["body", "include_inertia"],
    "catia_mirror": ["feature"],
    "catia_pad": [
        "direction",
        "limit",
        "second_length_mm",
        "thickness_mm",
        "thin",
        "up_to",
    ],
    "catia_pattern_circular": ["axis", "radius_mm"],
    "catia_pattern_rectangular": ["reversed"],
    "catia_pocket": [
        "direction",
        "limit",
        "reversed",
        "second_length_mm",
        "symmetric",
        "thickness_mm",
        "thin",
        "up_to",
    ],
    "catia_shaft": ["axis", "second_angle_deg", "thickness_mm", "thin"],
    "catia_shell": ["outward"],
    "catia_update": ["feature"],
}


def _accepted(method: Any) -> set[str] | None:
    """Keyword names a bound method takes, or None if it takes anything."""
    parameters = inspect.signature(method).parameters
    if any(p.kind is p.VAR_KEYWORD for p in parameters.values()):
        return None
    return {
        name
        for name, p in parameters.items()
        if p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD)
    } - {"self"}


def _sendable(operation: Any) -> set[str]:
    """Everything the daemon may forward for this operation.

    Server-*consumed* parameters are excluded: the server resolves them into a
    `server_fields` key and never forwards them, so a method that took them
    would be wrong rather than right.
    """
    return {p.name for p in operation.params if not p.consumed_by_server} | set(
        operation.server_fields
    )


def _narrower_than_declared() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for operation in OPERATIONS:
        if operation.server_only:
            continue
        method = getattr(CatiaCom, operation.method, None)
        if method is None:
            continue  # the other test owns this direction
        accepted = _accepted(method)
        if accepted is None:
            continue
        missing = sorted(_sendable(operation) - accepted)
        if missing:
            found[operation.name] = missing
    return found


class TestNoMixinIsShadowed:
    """A method on `CatiaCom` itself beats the mixin that supersedes it."""

    def test_no_operation_is_served_by_a_pre_registry_copy(self) -> None:
        """The exact fault behind the live `sketch_polygon` failure.

        `CatiaCom` defined its own `sketch_polygon` from before the registry
        rewrite, `SketcherMixin` defined the real one, and the MRO picked the
        one on the class. Nothing errors at import; the schema and the daemon
        table both come from the registry and are both correct; only the call
        fails, and only on a real seat.
        """
        shadowed = []
        for operation in OPERATIONS:
            if operation.server_only:
                continue
            method = operation.method
            if method not in vars(CatiaCom):
                continue
            owners = [mixin.__name__ for mixin in MIXINS if method in vars(mixin)]
            if owners:
                shadowed.append(f"{operation.name} -> CatiaCom.{method} shadows {owners}")
        assert shadowed == [], (
            "These operations are served by a copy on CatiaCom that hides the mixin "
            "meant to implement them. Delete the copy from catia_com.py:\n  "
            + "\n  ".join(shadowed)
        )

    def test_the_sketch_operations_come_from_the_sketcher_mixin(self) -> None:
        """Named directly, because these three are the ones that broke."""
        for name in ("sketch_circle", "sketch_rectangle", "sketch_polygon"):
            module = inspect.getmodule(getattr(CatiaCom, name))
            assert module is not None
            assert module.__name__.endswith("com.sketcher"), (
                f"{name} is being served by {module.__name__}, not the sketcher mixin."
            )


class TestEveryDeclaredArgumentCanBeReceived:
    @pytest.mark.parametrize(
        "operation",
        [op for op in OPERATIONS if not op.server_only and op.name not in KNOWN_NARROWER],
        ids=lambda op: op.name,
    )
    def test_the_method_takes_everything_the_schema_offers(self, operation: Any) -> None:
        method = getattr(CatiaCom, operation.method, None)
        if method is None:
            pytest.skip("covered by test_com_backend_covers_the_registry")
        accepted = _accepted(method)
        if accepted is None:
            return
        missing = sorted(_sendable(operation) - accepted)
        assert missing == [], (
            f"{operation.name} offers {missing} but {operation.method}() cannot take "
            f"{'it' if len(missing) == 1 else 'them'}. Either implement the option in "
            "the matching scripts/catia_bridge/com/ mixin, or remove it from the "
            "operation in app/catia/ops/."
        )


class TestTheDebtListIsExact:
    """A list that is allowed to be wrong is how the first drift survived."""

    def test_nothing_new_has_drifted(self) -> None:
        unexpected = {
            name: missing
            for name, missing in _narrower_than_declared().items()
            if name not in KNOWN_NARROWER
        }
        assert unexpected == {}, (
            "New signature drift, on top of the recorded debt: "
            f"{unexpected}. Implement it, or add it to KNOWN_NARROWER with a reason."
        )

    def test_nothing_recorded_has_quietly_been_fixed(self) -> None:
        """Delete the line when you close the gap, so the list keeps shrinking."""
        actual = _narrower_than_declared()
        stale = sorted(name for name in KNOWN_NARROWER if name not in actual)
        assert stale == [], (
            f"These now accept everything they declare: {stale}. Remove them from "
            "KNOWN_NARROWER."
        )

    def test_each_recorded_gap_is_still_exactly_what_is_recorded(self) -> None:
        actual = _narrower_than_declared()
        wrong = {
            name: {"recorded": expected, "actual": actual[name]}
            for name, expected in KNOWN_NARROWER.items()
            if name in actual and actual[name] != sorted(expected)
        }
        assert wrong == {}, f"The recorded gap no longer matches reality: {wrong}"

    def test_every_recorded_argument_is_optional(self) -> None:
        """A *required* argument the method cannot take would mean the operation
        never works at all, which is a broken tool rather than a missing option
        and does not belong on a debt list."""
        for name, options in KNOWN_NARROWER.items():
            operation = OPERATIONS_BY_NAME[name]
            required = {p.name for p in operation.params if p.required}
            assert not (set(options) & required), (
                f"{name} cannot take its own required argument(s) "
                f"{sorted(set(options) & required)} — it is broken, not merely narrow."
            )


class TestTheRefusalIsLegible:
    """What the engineer sees when they hit one of the recorded gaps."""

    class _Backend:
        is_mock = False

        def pad(self, *, sketch: str, length_mm: float) -> None: ...

        def anything(self, **kwargs: Any) -> None: ...

    def test_an_unimplemented_option_is_named(self) -> None:
        error = unimplemented_options(
            "catia_pad", "pad", self._Backend(), {"sketch": "s", "length_mm": 5, "up_to": "f"}
        )
        assert error is not None
        assert "'up_to'" in str(error)
        assert "does not implement" in str(error)

    def test_it_says_nothing_was_changed(self) -> None:
        """Checked before the call runs, so this is a fact and not a hope."""
        error = unimplemented_options(
            "catia_pad", "pad", self._Backend(), {"sketch": "s", "length_mm": 5, "thin": True}
        )
        assert error is not None
        assert "Nothing was changed" in str(error)

    def test_it_offers_the_way_forward(self) -> None:
        error = unimplemented_options(
            "catia_pad", "pad", self._Backend(), {"sketch": "s", "length_mm": 5, "thin": True}
        )
        assert error is not None
        assert "catia_run_command" in str(error)

    def test_several_options_read_as_plural(self) -> None:
        error = unimplemented_options(
            "catia_pad",
            "pad",
            self._Backend(),
            {"sketch": "s", "length_mm": 5, "thin": True, "up_to": "f"},
        )
        assert error is not None
        assert "those options" in str(error)
        assert "'thin', 'up_to'" in str(error)

    def test_a_call_the_method_can_take_is_not_refused(self) -> None:
        assert (
            unimplemented_options(
                "catia_pad", "pad", self._Backend(), {"sketch": "s", "length_mm": 5}
            )
            is None
        )

    def test_a_method_taking_kwargs_is_never_refused(self) -> None:
        """The mock and any future generic backend accept anything by design."""
        assert (
            unimplemented_options(
                "catia_thing", "anything", self._Backend(), {"whatever": 1}
            )
            is None
        )

    def test_a_mock_bridge_says_so(self) -> None:
        backend = self._Backend()
        backend.is_mock = True  # type: ignore[misc]
        error = unimplemented_options(
            "catia_pad", "pad", backend, {"sketch": "s", "length_mm": 5, "thin": True}
        )
        assert error is not None
        assert "mock bridge" in str(error)
