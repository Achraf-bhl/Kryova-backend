"""Running a compiled plan's calls against OCCT.

`OcctRunner` is a `CallRunner` — the seam `app.design.execute` already defines — so a
plan compiled by `app.design.compile` executes here with nothing in the design layer
knowing which kernel ran. That is Decision 1 of the master plan in one class, and it is
why this module contains no design logic and no geometry: it dispatches, and the
handlers in `operations/` do the work.

**Unimplemented operations raise `OperationNotSupported`, never a generic failure.**
Mapping 201 operations takes months, so at any moment most are missing. A missing
operation is a *known gap* the conformance harness counts as coverage; conflating it
with a real geometry failure would make that number meaningless.

**One runner per part.** The document lives in the context because a plan is a sequence
of calls that build one thing, and the OCAF labels that make naming work must persist
between them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.kernel.errors import OperationNotSupported
from app.kernel.measurement import Detail
from app.kernel.occt.binding import occt_version, require
from app.kernel.occt.document import PartDocument
from app.kernel.occt.operations import HANDLERS, BuildContext, coverage


class OcctRunner:
    """Executes plan calls against an OCCT-backed document.

    `detail` sets how much post-state each mutating call returns. `Detail.FULL` is the
    interactive default — the agent is prompted to react to what it sees and cannot
    react to a number it was not given. A bulk replay or a CI conformance run should
    lower it: measuring integrates over the whole shape, and at 10⁵ operations that is
    the run rather than a detail of it.
    """

    __slots__ = ("_context",)

    def __init__(self, *, detail: Detail = Detail.FULL) -> None:
        require()
        self._context = BuildContext(detail=detail)

    # -- the CallRunner contract ---------------------------------------------

    def __call__(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        handler = HANDLERS.get(tool)
        if handler is None:
            raise OperationNotSupported(tool)
        return handler(self._context, arguments)

    # -- introspection -------------------------------------------------------

    @property
    def document(self) -> PartDocument | None:
        """The part being built, or None before `catia_new_part`."""
        return self._context.document

    @property
    def detail(self) -> Detail:
        return self._context.detail

    @staticmethod
    def supported_tools() -> tuple[str, ...]:
        """What this backend can do today — the coverage list, honestly."""
        return tuple(sorted(HANDLERS))

    @staticmethod
    def coverage() -> dict[str, int]:
        """Implemented vs declared, as data rather than a claim."""
        return coverage()

    @staticmethod
    def backend_version() -> str:
        """The kernel version, for provenance records (master plan 7.3)."""
        return occt_version()


__all__ = ["OcctRunner"]
