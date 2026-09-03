"""The declarative CATIA operation registry.

One `Operation` per callable CATIA capability, declared in the domain module it
belongs to and assembled by `registry`. Import from here rather than from the
domain modules, so a later reshuffle of which module owns what does not break
call sites.
"""

from app.catia.ops.registry import (
    OPERATIONS,
    OPERATIONS_BY_NAME,
    TOOL_METHODS,
    by_tier,
    by_workbench,
    get,
    long_running_names,
    mutating_names,
    no_auto_checkpoint_names,
    summary,
)
from app.catia.ops.spec import Operation, Param, Tier, Workbench

__all__ = [
    "OPERATIONS",
    "OPERATIONS_BY_NAME",
    "TOOL_METHODS",
    "Operation",
    "Param",
    "Tier",
    "Workbench",
    "by_tier",
    "by_workbench",
    "get",
    "long_running_names",
    "mutating_names",
    "no_auto_checkpoint_names",
    "summary",
]
