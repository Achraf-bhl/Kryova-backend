"""Assembles every domain module into one authoritative operation table.

Adding a domain is one import and one entry in `_MODULES`. Adding an operation
is one `Operation(...)` in the domain module it belongs to and nothing else —
the tool schema, the daemon's validation table, the tier, the timeout class and
the docs all follow from it.

The checks in `_build` are the point of having a registry at all. Duplicate
names, a method two operations both claim, a destructive operation that forgot
its approval story: each of those used to be findable only by reading four
files side by side, and each has a one-line assertion here instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import ModuleType

from app.catia.ops import (
    assembly,
    drafting,
    infrastructure,
    inspection,
    knowledge,
    part_design,
    reference,
    sketcher,
    surfaces,
    ui,
    wireframe,
)
from app.catia.ops.spec import Operation, Tier, Workbench

#: Every module contributing operations, in the order they appear to the model.
#:
#: Order is not cosmetic: it is the order the tool list is presented in, and a
#: model reads a long list from the top. Infrastructure first (you need a
#: document before anything), then sketching, then the features that consume
#: sketches, then the wider workbenches, then the escape hatch last.
_MODULES: tuple[ModuleType, ...] = (
    infrastructure,
    inspection,
    sketcher,
    reference,
    part_design,
    wireframe,
    surfaces,
    assembly,
    drafting,
    knowledge,
    ui,
)


def _build(modules: Iterable[ModuleType]) -> tuple[Operation, ...]:
    """Flatten the modules into one table, refusing any inconsistency."""
    operations: list[Operation] = []
    by_name: dict[str, str] = {}
    by_method: dict[str, str] = {}

    for module in modules:
        declared = getattr(module, "OPERATIONS", None)
        if declared is None:
            raise RuntimeError(f"{module.__name__} declares no OPERATIONS tuple")
        for operation in declared:
            if operation.name in by_name:
                raise RuntimeError(
                    f"{operation.name} is declared in both {by_name[operation.name]} and "
                    f"{module.__name__}. A tool name must be unique across the registry."
                )
            if operation.method and operation.method in by_method:
                raise RuntimeError(
                    f"{operation.name} dispatches to backend method {operation.method!r}, "
                    f"which {by_method[operation.method]} already claims. Two tools sharing "
                    "one method means one of them silently does the other's job."
                )
            by_name[operation.name] = module.__name__
            if operation.method:
                by_method[operation.method] = operation.name
            operations.append(operation)

    return tuple(operations)


OPERATIONS: tuple[Operation, ...] = _build(_MODULES)

OPERATIONS_BY_NAME: dict[str, Operation] = {op.name: op for op in OPERATIONS}

#: Tool name -> backend method, the mapping the daemon dispatches through. Kept
#: as data so a frame can never name a method that is not on this list.
#: Server-only operations are absent by construction: there is nothing on a
#: workstation for them to reach.
TOOL_METHODS: dict[str, str] = {op.name: op.method for op in OPERATIONS if not op.server_only}

#: Operations the server answers itself. A frame naming one of these arrived
#: from somewhere it should not have, and is refused rather than run.
SERVER_ONLY: frozenset[str] = frozenset(op.name for op in OPERATIONS if op.server_only)


def get(name: str) -> Operation | None:
    return OPERATIONS_BY_NAME.get(name)


def by_workbench(workbench: Workbench) -> tuple[Operation, ...]:
    return tuple(op for op in OPERATIONS if op.workbench is workbench)


def by_tier(tier: Tier) -> tuple[Operation, ...]:
    return tuple(op for op in OPERATIONS if op.tier is tier)


def mutating_names() -> frozenset[str]:
    return frozenset(op.name for op in OPERATIONS if op.mutating)


def long_running_names() -> frozenset[str]:
    return frozenset(op.name for op in OPERATIONS if op.long_running)


def no_auto_checkpoint_names() -> frozenset[str]:
    return frozenset(op.name for op in OPERATIONS if op.no_auto_checkpoint)


def summary() -> dict[str, int]:
    """Counts by tier and workbench — what the coverage report reads."""
    counts: dict[str, int] = {"total": len(OPERATIONS)}
    for tier in Tier:
        counts[f"tier:{tier.value}"] = len(by_tier(tier))
    for workbench in Workbench:
        found = len(by_workbench(workbench))
        if found:
            counts[f"workbench:{workbench.value}"] = found
    return counts
