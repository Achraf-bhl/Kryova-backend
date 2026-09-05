"""Which geometry kernel executes a tool call.

Lives here rather than under `app/catia/` because it is the thing that *chooses
between* CATIA and OCCT — filing it under one of the two backends it selects would
say the opposite of what it does. `app/geometry/` is the backend-neutral half of the
geometry layer, which is exactly what this is.

**Decision 1, finally wired up.** The master plan says the design IR compiles to an
open kernel *first* and that CATIA is one backend among several. Until this module
existed that was true of `app/design/` and `app/kernel/` and false of the product:
`dispatch.call_catia` went straight to the bridge, `OcctRunner` was constructed only
by tests, and so 108 working operations needed a CATIA licence to reach. The agent
could not build a box without a seat.

This is the seam that fixes it, and it is deliberately small. `dispatch` asks which
backend is selected and, for OCCT, hands the call here instead of to a device. The
CATIA path is untouched — same validation, same approval, same logging, same
messages — because that path is the most load-bearing in the application and a
rewrite of it to gain a second backend would be a bad trade.

Three things about this are worth knowing before changing it:

**The session is per-worker and cannot be otherwise.** A `PartDocument` is live
in-memory OCCT state with OCAF labels; it is not serialisable to Redis and a
different worker cannot resume it. So a conversation building geometry on the OCCT
backend is pinned to the process that started it. On the desktop deployment — one
process — that is simply how it works. A multi-worker deployment needs sticky
routing per conversation, and the honest status of that today is that it is not
handled: `catia_status` reports the backend and the session, so at least it is
visible rather than silently wrong.

**The tools keep their `catia_` prefix even when CATIA is nowhere in sight**, and
that is a knowing compromise rather than an oversight. The prefix means "goes to the
workstation" everywhere else in this codebase (`app/ai/resume.py` depends on it, and
`tests/test_tool_registry.py` enforces it), and 201 tool names are woven through the
registry, the specs, the prompts and the frozen system prompts. Renaming them to
reach a second backend would be a far larger and riskier change than the one this
module makes. Recorded here so nobody has to rediscover the reasoning.

**A tool OCCT has not implemented is `OperationNotSupported`, not a failure.** It is
translated to a `CatiaError` that says the operation is not in this backend yet and
names the other backend, because "not implemented here" and "your geometry is wrong"
are different things and an agent told the second will damage a good part trying to
fix it.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any, Final, Literal

from app.core.config import settings

logger = logging.getLogger(__name__)

Backend = Literal["catia", "occt"]

#: Backends this build knows. `catia` drives a real seat over the bridge; `occt`
#: builds in this process with the open kernel and needs no licence, no Windows and
#: no network.
BACKENDS: Final[tuple[Backend, ...]] = ("catia", "occt")

#: How many OCCT documents to hold at once. Each is live B-rep in memory and a
#: complex part is tens of megabytes, so this is a memory bound rather than a
#: performance one. Least-recently-used is evicted; an evicted conversation gets a
#: fresh document and is *told*, rather than quietly continuing against an empty one.
MAX_SESSIONS: Final = 8

#: Conversations with a live OCCT document, most-recently-used last.
_sessions: OrderedDict[str, Any] = OrderedDict()

#: Conversations whose document was evicted. Kept so the next call can say so
#: instead of behaving as though the part had never been built.
_evicted: set[str] = set()


def selected_backend() -> Backend:
    """Which backend this deployment drives, from settings.

    A setting rather than automatic fallback, and that is the important part: a
    deployment that silently switched to OCCT when no seat answered would give the
    user a part built by a different kernel without saying so, and Decision 3 says a
    result is bound to what produced it. Choosing is cheap; guessing is not
    recoverable.
    """
    choice = (settings.geometry_backend or "catia").strip().lower()
    if choice not in BACKENDS:
        logger.warning(
            "GEOMETRY_BACKEND=%r is not a backend; falling back to catia. Valid: %s",
            settings.geometry_backend,
            ", ".join(BACKENDS),
        )
        return "catia"
    return choice  # type: ignore[return-value]


def is_local() -> bool:
    """True when tool calls are executed in this process rather than on a seat."""
    return selected_backend() == "occt"


def local_tool_names() -> frozenset[str]:
    """What the OCCT backend actually implements, honestly.

    Read from the handler table rather than declared, so the number cannot drift
    from the code. Offering the agent all 201 when 108 work costs it a turn per
    miss and teaches it nothing.
    """
    try:
        from app.kernel.occt.runner import OcctRunner

        return frozenset(OcctRunner.supported_tools())
    except Exception as exc:  # noqa: BLE001 - a missing kernel is a state, not a crash
        logger.warning("The OCCT kernel is not usable: %s", exc)
        return frozenset()


def local_coverage() -> dict[str, int]:
    """Implemented vs declared, as data. Surfaced by `catia_status`."""
    try:
        from app.kernel.occt.runner import OcctRunner

        return OcctRunner.coverage()
    except Exception:  # noqa: BLE001
        return {}


def backend_version() -> str:
    """The kernel version, for the provenance a result is bound to."""
    try:
        from app.kernel.occt.runner import OcctRunner

        return OcctRunner.backend_version()
    except Exception as exc:  # noqa: BLE001
        return f"unavailable ({exc})"


def session_for(conversation_id: str | None) -> Any:
    """The `OcctRunner` building this conversation's part, created on first use.

    Keyed by conversation because a plan is a sequence of calls that build one
    thing and the OCAF labels making `feature#selector` work must persist between
    them — the same reason `OcctRunner` documents "one runner per part".

    `None` gets its own scratch session rather than an error: a status query or a
    one-off measurement outside any conversation is a reasonable thing to do, and it
    simply does not accumulate.
    """
    from app.kernel.occt.runner import OcctRunner

    key = conversation_id or "__scratch__"
    existing = _sessions.get(key)
    if existing is not None:
        _sessions.move_to_end(key)
        return existing

    runner = OcctRunner()
    _sessions[key] = runner
    while len(_sessions) > MAX_SESSIONS:
        dropped, _ = _sessions.popitem(last=False)
        _evicted.add(dropped)
        logger.info("Evicted the OCCT document for conversation %s", dropped)
    return runner


def peek_session(conversation_id: str | None) -> Any | None:
    """The runner for this conversation if one exists, **without creating one**.

    Separate from `session_for` because a status query must not build a document
    as a side effect: polling the panel would otherwise fill the session table
    with empty documents and evict the real ones.
    """
    return _sessions.get(conversation_id or "__scratch__")


def was_evicted(conversation_id: str | None) -> bool:
    """Whether this conversation's document was dropped to make room.

    Asked *before* a fresh session is handed out, so the caller can say "the part
    you were building is gone" rather than letting the agent add a pocket to an
    empty document and report success.
    """
    return (conversation_id or "__scratch__") in _evicted


def forget(conversation_id: str | None) -> None:
    """Drop a conversation's document — on close, or to start again."""
    key = conversation_id or "__scratch__"
    _sessions.pop(key, None)
    _evicted.discard(key)


def clear_eviction(conversation_id: str | None) -> None:
    """Acknowledge that the user has been told their document was evicted."""
    _evicted.discard(conversation_id or "__scratch__")


def session_count() -> int:
    return len(_sessions)


__all__ = [
    "BACKENDS",
    "MAX_SESSIONS",
    "Backend",
    "backend_version",
    "clear_eviction",
    "forget",
    "is_local",
    "peek_session",
    "local_coverage",
    "local_tool_names",
    "selected_backend",
    "session_count",
    "session_for",
    "was_evicted",
]
