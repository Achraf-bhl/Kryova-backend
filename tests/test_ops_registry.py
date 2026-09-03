"""The operation registry's own guarantees — the ones nothing else can check.

The registry is the single declaration that four consumers read: the model's
tool schema, the daemon's independent re-validation table, the backend method
each call dispatches to, and the licence gating that keys off the workbench.
Before it existed those were four hand-written copies of the same facts, which
is why the tool count stopped at 39.

What that buys is only real if the registry actually refuses the inconsistencies
it claims to. `_build` has three assertions in it and, until this file, none of
them had ever run — a duplicate operation name or two operations claiming one
backend method would have been discovered by a wrong part rather than by an
import error. That is what these tests exercise.

The query helpers are here for a plainer reason: `dispatch` and `tool_specs`
both read them, and a helper that quietly returns the wrong set is a tier
mistake or a missing checkpoint rather than a visible failure.

Offline: no database fixture, no CATIA.
"""

import types

import pytest

from app.catia.ops import registry
from app.catia.ops.spec import Operation, Tier, Workbench, length, optional, required


def module_of(*operations: Operation) -> types.ModuleType:
    """A throwaway module carrying an OPERATIONS tuple, as `_build` expects."""
    module = types.ModuleType("tests.fake_ops")
    module.OPERATIONS = operations  # type: ignore[attr-defined]
    return module


def op(name: str, *, method: str = "", tier: Tier = Tier.WRITE) -> Operation:
    return Operation(
        name=name,
        summary="A test operation.",
        tier=tier,
        workbench=Workbench.PART_DESIGN,
        method=method,
        params=(required("length_mm", length("How far.")),),
    )


class TestTheBuildRefusesInconsistency:
    """Three assertions that had never run. Each was findable only by reading
    four files side by side, which is the whole reason the registry exists."""

    def test_a_module_with_no_operations_tuple_is_refused(self) -> None:
        empty = types.ModuleType("tests.no_operations")
        with pytest.raises(RuntimeError, match="declares no OPERATIONS"):
            registry._build([empty])

    def test_two_operations_cannot_share_a_name(self) -> None:
        """A duplicate name means one of the two is silently unreachable."""
        with pytest.raises(RuntimeError, match="must be unique"):
            registry._build([module_of(op("catia_thing")), module_of(op("catia_thing"))])

    def test_a_duplicate_name_names_both_modules(self) -> None:
        with pytest.raises(RuntimeError) as caught:
            registry._build([module_of(op("catia_thing")), module_of(op("catia_thing"))])
        assert "tests.fake_ops" in str(caught.value)

    def test_two_operations_cannot_claim_one_backend_method(self) -> None:
        """Two tools sharing a method means one silently does the other's job."""
        with pytest.raises(RuntimeError, match="already claims"):
            registry._build(
                [module_of(op("catia_alpha", method="shared"), op("catia_beta", method="shared"))]
            )

    def test_a_consistent_module_builds(self) -> None:
        built = registry._build([module_of(op("catia_alpha"), op("catia_beta"))])
        assert [operation.name for operation in built] == ["catia_alpha", "catia_beta"]

    def test_the_method_defaults_to_the_name_without_its_prefix(self) -> None:
        built = registry._build([module_of(op("catia_alpha"))])
        assert built[0].method == "alpha"


class TestTheRealRegistry:
    def test_it_has_operations_and_they_are_uniquely_named(self) -> None:
        names = [operation.name for operation in registry.OPERATIONS]
        assert names
        assert len(set(names)) == len(names)

    def test_every_name_is_reachable_by_lookup(self) -> None:
        for operation in registry.OPERATIONS:
            assert registry.get(operation.name) is operation

    def test_an_unknown_name_returns_none_rather_than_raising(self) -> None:
        assert registry.get("catia_not_a_real_operation") is None

    def test_tool_methods_covers_everything_that_leaves_the_server(self) -> None:
        """Server-only operations are absent by construction — there is nothing
        on a workstation for them to reach, and a frame naming one is a fault."""
        sendable = {op.name for op in registry.OPERATIONS if not op.server_only}
        assert set(registry.TOOL_METHODS) == sendable
        assert registry.SERVER_ONLY.isdisjoint(registry.TOOL_METHODS)

    def test_every_server_only_operation_declares_no_method(self) -> None:
        for name in registry.SERVER_ONLY:
            assert registry.OPERATIONS_BY_NAME[name].method == ""


class TestQueries:
    def test_by_workbench_returns_only_that_workbench(self) -> None:
        found = registry.by_workbench(Workbench.SKETCHER)
        assert found
        assert all(operation.workbench is Workbench.SKETCHER for operation in found)

    def test_by_workbench_is_empty_for_one_nothing_is_declared_in(self) -> None:
        """An empty answer, not a KeyError — `summary` counts on it."""
        declared = {operation.workbench for operation in registry.OPERATIONS}
        unused = [bench for bench in Workbench if bench not in declared]
        if not unused:  # pragma: no cover - only if every workbench gains an op
            pytest.skip("every workbench now has operations")
        assert registry.by_workbench(unused[0]) == ()

    def test_by_tier_partitions_the_registry(self) -> None:
        total = sum(len(registry.by_tier(tier)) for tier in Tier)
        assert total == len(registry.OPERATIONS)

    def test_mutating_names_is_everything_that_is_not_a_read(self) -> None:
        """`dispatch` gates approval on this, so a wrong set is an auth hole."""
        mutating = registry.mutating_names()
        assert mutating == {op.name for op in registry.OPERATIONS if op.tier is not Tier.READ}
        assert all(registry.OPERATIONS_BY_NAME[name].mutating for name in mutating)

    def test_long_running_names_are_all_declared_long_running(self) -> None:
        for name in registry.long_running_names():
            assert registry.OPERATIONS_BY_NAME[name].long_running

    def test_no_auto_checkpoint_names_are_all_declared_that_way(self) -> None:
        """A checkpoint is a COM save, and a failed one refuses the call — so
        the tools that dismiss a stuck dialog must be in here or they can only
        run when no dialog is stuck."""
        for name in registry.no_auto_checkpoint_names():
            assert registry.OPERATIONS_BY_NAME[name].no_auto_checkpoint

    def test_the_three_name_sets_are_frozen(self) -> None:
        """They are read at import by `dispatch`; a mutable answer is a foot-gun."""
        for produce in (
            registry.mutating_names,
            registry.long_running_names,
            registry.no_auto_checkpoint_names,
        ):
            assert isinstance(produce(), frozenset)


class TestSummary:
    """What the coverage report reads. It had no test at all."""

    def test_the_total_is_the_number_of_operations(self) -> None:
        assert registry.summary()["total"] == len(registry.OPERATIONS)

    def test_every_tier_is_reported_even_when_it_is_empty(self) -> None:
        counts = registry.summary()
        for tier in Tier:
            assert f"tier:{tier.value}" in counts

    def test_the_tier_counts_add_up_to_the_total(self) -> None:
        counts = registry.summary()
        assert sum(counts[f"tier:{tier.value}"] for tier in Tier) == counts["total"]

    def test_only_workbenches_with_operations_are_reported(self) -> None:
        """A workbench with a zero beside it reads as 'wired but empty', which
        is a different and wrong claim from 'not wired yet'."""
        counts = registry.summary()
        for key, value in counts.items():
            if key.startswith("workbench:"):
                assert value > 0

    def test_the_workbench_counts_add_up_to_the_total(self) -> None:
        counts = registry.summary()
        benches = sum(value for key, value in counts.items() if key.startswith("workbench:"))
        assert benches == counts["total"]


class TestOperationOrderIsDeliberate:
    def test_infrastructure_comes_first(self) -> None:
        """Order is what the model reads from the top: you need a document
        before anything, and the escape hatch belongs last."""
        first = registry.OPERATIONS[0]
        assert first.workbench is Workbench.INFRASTRUCTURE

    def test_rebuilding_from_the_declared_modules_reproduces_the_table(self) -> None:
        """The table is a pure function of `_MODULES` and their order.

        Which is the property that makes the order meaningful at all: if the
        result depended on anything else, "infrastructure first" would be a
        comment rather than a fact.
        """
        assert registry._build(registry._MODULES) == registry.OPERATIONS


class TestParameterConsistency:
    """Facts about every operation that only hold across the whole table."""

    def test_no_operation_both_supplies_and_consumes_a_parameter(self) -> None:
        for operation in registry.OPERATIONS:
            for param in operation.params:
                assert not (param.supplied_by_server and param.consumed_by_server), operation.name

    def test_every_parameter_carries_a_description(self) -> None:
        """The model reads these to choose values; an undescribed parameter is
        one it will guess at."""
        for operation in registry.OPERATIONS:
            for param in operation.params:
                assert param.schema.get("description"), f"{operation.name}.{param.name}"

    def test_no_parameter_is_also_a_server_field(self) -> None:
        for operation in registry.OPERATIONS:
            assert not ({p.name for p in operation.params} & set(operation.server_fields)), (
                operation.name
            )

    def test_the_model_schema_hides_server_supplied_parameters(self) -> None:
        for operation in registry.OPERATIONS:
            properties = set(operation.json_schema()["properties"])
            for name in operation.server_supplied_fields:
                assert name not in properties, f"{operation.name}.{name}"

    def test_the_daemon_schema_hides_server_consumed_parameters(self) -> None:
        for operation in registry.OPERATIONS:
            properties = set(operation.daemon_schema()["properties"])
            for param in operation.params:
                if param.consumed_by_server:
                    assert param.name not in properties, f"{operation.name}.{param.name}"

    def test_every_schema_closes_additional_properties(self) -> None:
        """An unknown field means the model has misunderstood the tool, and
        accepting it means the daemon silently drops what it actually meant."""
        for operation in registry.OPERATIONS:
            assert operation.json_schema()["additionalProperties"] is False
            assert operation.daemon_schema()["additionalProperties"] is False


def test_required_and_optional_land_in_the_right_half_of_the_schema() -> None:
    operation = Operation(
        name="catia_thing",
        summary="A test operation.",
        tier=Tier.WRITE,
        workbench=Workbench.PART_DESIGN,
        params=(
            required("length_mm", length("How far.")),
            optional("thin", {"type": "boolean", "description": "Thin-walled."}),
        ),
    )
    schema = operation.json_schema()
    assert schema["required"] == ["length_mm"]
    assert set(schema["properties"]) == {"length_mm", "thin"}
