"""The process-wide handle, with the same contract the retrieval service has.

**Nothing here raises.** Consulting the knowledge base improves an answer; it is
never the reason a user does not get one. A lookup that fails comes back empty
and is logged once, exactly like `app.retrieval.service` -- the two are read by
the same agent turn, and a turn that survives a missing index but dies on a
missing entry would be a strange kind of robust.

The registry itself is pure and cached, so this class holds no state beyond the
settings it was built with. It exists to give callers one place to reach and to
keep the try/except discipline in one file rather than at every call site.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Sequence

# Imported from the submodule by name rather than as `app.catia_kb.brief`: the
# package `__init__` re-exports a *function* called `brief`, which would shadow
# the module of the same name and turn every call here into an AttributeError.
from app.catia_kb.brief import brief as render_brief
from app.catia_kb.brief import describe
from app.catia_kb.languages import normalise_language
from app.catia_kb.recognise import Recognition, expand_query, recognise
from app.catia_kb.registry import registry
from app.catia_kb.types import Entry, Kind
from app.core.config import settings

logger = logging.getLogger(__name__)


class CatiaKnowledge:
    """Fault-tolerant access to the CATIA reference data."""

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._warned = False

    # -- lifecycle ----------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether there is anything to look in. Never raises."""
        if not self._enabled:
            return False
        try:
            return len(registry()) > 0
        except Exception:  # noqa: BLE001 - a data defect must not stop the server
            self._warn_once()
            return False

    def _warn_once(self) -> None:
        if not self._warned:
            self._warned = True
            logger.exception("The CATIA knowledge base failed to build; it will be skipped.")

    # -- reading ------------------------------------------------------------

    def find(self, text: str, *, limit: int = 12, assume_catia: bool = False) -> Recognition:
        """Recognise CATIA entities in free text. `Recognition()` on any failure."""
        if not self.available or not text:
            return Recognition()
        try:
            return recognise(text, limit=limit, assume_catia=assume_catia)
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.exception("CATIA recognition failed for %r", text[:120])
            return Recognition()

    def lookup(self, term: str, *, language: str | None = None, limit: int = 4) -> list[dict[str, Any]]:
        """Full records for a named term, best first. `[]` when nothing matches."""
        if not self.available or not term or not term.strip():
            return []
        try:
            index = registry()
            direct = index.lookup(term)
            if not direct:
                found = recognise(term, limit=limit, assume_catia=True)
                direct = tuple(found.entries())
            return [describe(entry, language=language) for entry in direct[:limit]]
        except Exception:  # noqa: BLE001
            logger.exception("CATIA lookup failed for %r", term[:120])
            return []

    def disambiguation(self, term: str) -> dict[str, Any] | None:
        """The fork for an ambiguous term, when there is one."""
        if not self.available:
            return None
        try:
            fork = registry().disambiguation(term)
        except Exception:  # noqa: BLE001
            logger.exception("CATIA disambiguation failed for %r", term[:120])
            return None
        return fork.to_dict() if fork else None

    def brief(self, text: str, *, language: str | None = None) -> str:
        """The compact block for a state message. `''` when nothing was found."""
        if not self.available or not text:
            return ""
        try:
            return render_brief(text, language=normalise_language(language))
        except Exception:  # noqa: BLE001
            logger.exception("CATIA brief failed")
            return ""

    def expand(self, query: str, *, language: str | None = None) -> str:
        """Add cross-language terms to a retrieval query. Returns `query` unchanged on failure."""
        if not self.available or not query:
            return query
        try:
            return expand_query(query, language=language)
        except Exception:  # noqa: BLE001
            logger.exception("CATIA query expansion failed for %r", query[:120])
            return query

    def entries_of_kind(self, kind: Kind) -> Sequence[Entry]:
        if not self.available:
            return ()
        try:
            return registry().by_kind(kind)
        except Exception:  # noqa: BLE001
            logger.exception("CATIA kind listing failed")
            return ()

    def stats(self) -> dict[str, Any]:
        """Coverage counts, for the health endpoint."""
        if not self._enabled:
            return {"available": False, "reason": "disabled"}
        try:
            data: dict[str, Any] = {"available": True}
            data.update(registry().stats())
            return data
        except Exception:  # noqa: BLE001
            self._warn_once()
            return {"available": False, "reason": "failed to build"}


_service: CatiaKnowledge | None = None
_lock = threading.Lock()


def catia_knowledge() -> CatiaKnowledge:
    """The process-wide handle, built from settings on first use."""
    global _service
    if _service is not None:
        return _service
    with _lock:
        if _service is None:
            _service = CatiaKnowledge(enabled=getattr(settings, "catia_knowledge_enabled", True))
    return _service


def reset_catia_knowledge() -> None:
    """Forget the singleton. For tests, which flip the setting."""
    global _service
    with _lock:
        _service = None
