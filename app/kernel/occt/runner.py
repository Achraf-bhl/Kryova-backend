"""Running a compiled plan's calls against OCCT.

`OcctRunner` is a `CallRunner` — the seam `app.design.execute` already defines — so a
plan compiled by `app.design.compile` executes here with nothing in the design layer
knowing which kernel ran. That is Decision 1 of the master plan in one class, and it is
why this module contains no design logic and no geometry: it dispatches, and the
handlers in `operations/` do the work.

**Unimplemented operations raise `OperationNotSupported`, never a generic failure.**
Most of the registry is not implemented here, and a missing operation is a *known gap*
the conformance harness counts as coverage; conflating it with a real geometry failure
would make that number meaningless. Each one carries its reason from `refusals`.

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
from app.kernel.occt.operations import HANDLERS, RECORDED, BuildContext, coverage
from app.kernel.occt.refusals import reason_for
from app.kernel.occt.unsupported import refuse_unhonoured


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
            # With its reason: an interface-only operation, one another part of the
            # product does, or one nobody has needed yet. See `refusals`.
            raise OperationNotSupported(tool, reason_for(tool))
        # An advertised argument this backend cannot act on is refused here rather
        # than dropped in the handler. Before 2026-09-11 eighteen of them were
        # silently ignored, and every one produced a confident `ok` describing
        # geometry nobody asked for — `catia_pad(thin=True)` returning a solid,
        # `catia_translate(distance_mm=50)` moving 1 mm. See `unsupported`.
        refuse_unhonoured(tool, arguments)
        result = handler(self._context, arguments)
        if tool in RECORDED:
            # After the handler, deliberately: a call that raised changed nothing,
            # and replaying a failure would only fail again — taking the part with
            # it, since a replay rebuilds from the top. See
            # `app.kernel.occt.operations.parameters` for what the journal is for.
            self._context.record(tool, arguments, result)
        return result

    # -- introspection -------------------------------------------------------

    @property
    def document(self) -> PartDocument | None:
        """The part being built, or None before `catia_new_part`."""
        return self._context.document

    @property
    def assembly(self) -> Any:
        """The assembly being composed, or None before `catia_product_create`.

        An `app.kernel.occt.operations.assembly_ops.AssemblyState`, typed `Any` for the
        reason `BuildContext.assembly` is: that module reaches `app.assembly`, which
        reaches `app.dynamics.pose`, and a runner that imported it at module scope would
        drag the whole chain into every geometry test.

        A *property* rather than a reach into `_context` from a caller: the assembly is
        the second thing a conversation can hold, and a route that read a private slot
        would be the first place outside this class to know the runner's shape.
        """
        return self._context.assembly

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
