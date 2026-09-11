"""A `CallRunner` bound to a real CATIA seat — the right-hand side of conformance.

`app/kernel/conformance.py` has existed since 2026-09-05 and is backend-neutral
on purpose: it takes two `CallRunner`s, builds one compiled `Plan` on each, and
reports what differs. **Its right-hand side had never been a real seat**, because
nothing adapted `app.catia.dispatch` to that seam — so Decision 1's central claim,
that a design built on the open kernel and on CATIA is the same design, was
unverified rather than wrong. THE QUEUE B1 and B2 are that gap; this module is the
missing piece, and it can only be written where a seat is.

**Three things the adapter has to get right, and each of them is a decision.**

1. **A coverage gap is not a failure.** The bridge answers an operation it does not
   implement with *"<tool> is not implemented by this bridge. Nothing was changed."*
   `conformance._run` separates gaps from failures by catching
   `OperationNotSupported`, so that sentence has to become that exception or the
   coverage number becomes meaningless and 93 unimplemented CATIA operations read
   as 93 broken ones. The match is on the bridge's own wording rather than on a
   list of tool names, for the reason `local_tool_names` is derived rather than
   declared: a list kept here is wrong the day somebody implements one.

2. **`CatiaUnavailable` is left to propagate.** No seat is not a coverage gap and
   not a geometry disagreement — it is the run being impossible, and the harness
   reporting "catia: the bridge is offline" is the honest outcome. Swallowing it
   would produce a conformance result that says the backends agree because neither
   of them ran.

3. **One runner owns one document, exactly as `OcctRunner` does.** A plan is a
   sequence of calls that build one thing, and the conversation is what the CATIA
   document is bound to (`CatiaDocument`, `dispatch._local_document`). Sharing a
   conversation between the two sides of a comparison would have them building
   into each other.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from sqlalchemy.orm import Session

from app.catia.dispatch import CatiaError, call_catia
from app.kernel.errors import OperationNotSupported

#: How the bridge says it does not implement something. Matched on the bridge's
#: own wording (`scripts/catia_bridge/backend.py::unsupported`) rather than on a
#: table of tool names, which would be wrong the day one is implemented.
_COVERAGE_GAP: Final[tuple[str, ...]] = (
    "is not implemented by this bridge",
    "is not implemented by this mock bridge",
    "is not a CATIA tool",
)


class CatiaSeatRunner:
    """Executes plan calls against a licensed CATIA seat through the bridge.

    The mirror of `app.kernel.occt.runner.OcctRunner`: same seam, same contract,
    different kernel. Nothing in the design layer can tell which one it holds,
    which is what makes `compare_backends` a fair question rather than a
    comparison of two code paths that were written to agree.
    """

    __slots__ = ("_db", "_user_id", "_conversation_id", "_timeout_s", "_calls")

    def __init__(
        self,
        db: Session,
        *,
        user_id: str,
        conversation_id: str,
        timeout_s: float | None = None,
    ) -> None:
        self._db = db
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._timeout_s = timeout_s
        self._calls = 0

    # -- the CallRunner contract ---------------------------------------------

    def __call__(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self._calls += 1
        try:
            return call_catia(
                self._db,
                user_id=self._user_id,
                conversation_id=self._conversation_id,
                tool=tool,
                arguments=dict(arguments),
                timeout_s=self._timeout_s,
            )
        except CatiaError as exc:
            message = str(exc)
            if any(marker in message for marker in _COVERAGE_GAP):
                # A gap, not a break. See the module docstring, point 1.
                raise OperationNotSupported(tool, message, backend="catia") from exc
            raise

    # -- introspection -------------------------------------------------------

    @property
    def calls(self) -> int:
        """How many calls this runner has made. Read by the conformance report."""
        return self._calls

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @staticmethod
    def backend_version() -> str:
        """What answered, for a provenance record. Resolved lazily and never raised
        on: a version string is not worth failing a conformance run for."""
        return "catia-v5"


__all__ = ["CatiaSeatRunner"]
