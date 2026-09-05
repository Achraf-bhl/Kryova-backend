"""The geometry backend seam — Decision 1 made true of the product.

Until this existed, `dispatch.call_catia` went straight to the bridge and
`OcctRunner` was constructed only by tests: 108 working kernel operations needed a
CATIA licence to reach, and the agent could not build a box without a seat. These
tests pin the seam and, more importantly, pin the things it must never do quietly.

Offline: the OCCT half needs the kernel (skipped without it), and the selection,
session and honesty tests need nothing at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import settings
from app.geometry import backends


@pytest.fixture(autouse=True)
def _clean_sessions() -> Any:
    """Sessions are process-global by necessity; a test must not inherit one."""
    for key in list(backends._sessions):
        backends.forget(key)
    yield
    for key in list(backends._sessions):
        backends.forget(key)


@pytest.fixture
def occt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "geometry_backend", "occt")


def _kernel_available() -> bool:
    try:
        from app.kernel.occt.binding import require

        require()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_kernel = pytest.mark.skipif(not _kernel_available(), reason="OCCT not installed")


class TestChoosingABackend:
    def test_the_default_is_catia_so_an_existing_deployment_is_unchanged(self) -> None:
        assert "catia" in backends.BACKENDS
        assert backends.selected_backend() in backends.BACKENDS

    def test_occt_is_selected_by_the_setting(self, occt: None) -> None:
        assert backends.selected_backend() == "occt"
        assert backends.is_local() is True

    def test_an_unknown_backend_falls_back_to_catia_rather_than_crashing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo in an env var must not take the whole geometry surface down."""
        monkeypatch.setattr(settings, "geometry_backend", "opencascade")
        assert backends.selected_backend() == "catia"

    def test_the_choice_is_never_automatic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A silent fallback would hand the user a part built by a different kernel.

        There is deliberately no "use OCCT when no seat answers" path: Decision 3
        binds a result to what produced it, and a backend that switched itself
        would make that unknowable from the outside. This test exists to fail if
        someone adds the convenience later.
        """
        monkeypatch.setattr(settings, "geometry_backend", "catia")
        assert backends.is_local() is False


class TestSessionsAreOnePartEach:
    def test_a_conversation_keeps_one_document_across_calls(self, occt: None) -> None:
        """OCAF labels must persist between calls or feature#selector cannot work."""
        first = backends.session_for("conversation-a")
        assert backends.session_for("conversation-a") is first

    def test_two_conversations_do_not_share_a_part(self, occt: None) -> None:
        assert backends.session_for("a") is not backends.session_for("b")

    def test_peeking_does_not_create_a_document(self, occt: None) -> None:
        """A status poll must not fill the table with empty parts."""
        assert backends.peek_session("never-touched") is None
        assert backends.session_count() == 0

    def test_the_oldest_document_is_evicted_and_the_eviction_is_remembered(
        self, occt: None
    ) -> None:
        """Silence here lets the agent add a pocket to an empty part and report success."""
        for index in range(backends.MAX_SESSIONS + 1):
            backends.session_for(f"conversation-{index}")
        assert backends.session_count() == backends.MAX_SESSIONS
        assert backends.was_evicted("conversation-0") is True
        assert backends.was_evicted("conversation-1") is False

    def test_using_a_conversation_keeps_it_from_being_evicted(self, occt: None) -> None:
        backends.session_for("keep-me")
        for index in range(backends.MAX_SESSIONS):
            backends.session_for(f"filler-{index}")
            backends.session_for("keep-me")  # touching it moves it to the end
        assert backends.was_evicted("keep-me") is False

    def test_an_eviction_can_be_acknowledged_once(self, occt: None) -> None:
        for index in range(backends.MAX_SESSIONS + 1):
            backends.session_for(f"c{index}")
        assert backends.was_evicted("c0") is True
        backends.clear_eviction("c0")
        assert backends.was_evicted("c0") is False

    def test_forget_drops_both_the_session_and_its_eviction(self, occt: None) -> None:
        backends.session_for("gone")
        backends.forget("gone")
        assert backends.peek_session("gone") is None
        assert backends.was_evicted("gone") is False


class TestCoverageIsReadNotDeclared:
    @needs_kernel
    def test_the_offered_tools_come_from_the_handler_table(self) -> None:
        """A declared number drifts from the code; a read one cannot."""
        names = backends.local_tool_names()
        assert names
        assert "catia_pad" in names
        coverage = backends.local_coverage()
        assert coverage["implemented"] == len(names)
        assert coverage["declared"] > coverage["implemented"]

    @needs_kernel
    def test_the_kernel_version_is_reported_for_provenance(self) -> None:
        assert "OCCT" in backends.backend_version()

    def test_a_missing_kernel_is_a_state_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Everything must import on a machine with no OCCT — app/kernel's own contract."""
        import builtins

        real = builtins.__import__

        def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("app.kernel"):
                raise ModuleNotFoundError(name)
            return real(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)
        assert backends.local_tool_names() == frozenset()
        assert backends.local_coverage() == {}
        assert "unavailable" in backends.backend_version()


@needs_kernel
class TestTheAgentCanBuildWithoutASeat:
    """The whole point: 60x40x20 with no CATIA, no licence and no Windows."""

    def test_a_part_builds_and_measures_to_the_closed_form_answer(self, occt: None) -> None:
        runner = backends.session_for("build")
        runner("catia_new_part", {"name": "Bracket"})
        runner("catia_sketch_create", {"support": "XY", "name": "profile"})
        runner("catia_sketch_rectangle", {"sketch": "profile", "width_mm": 60.0, "height_mm": 40.0})
        runner("catia_pad", {"name": "slab", "sketch": "profile", "length_mm": 20.0})

        assert runner("catia_list_faces", {})["count"] == 6
        assert runner("catia_measure", {})["volume_mm3"] == pytest.approx(48_000.0)

    def test_an_unimplemented_operation_says_so_rather_than_failing_the_part(
        self, occt: None
    ) -> None:
        """'Not implemented here' and 'your geometry is wrong' need different answers.

        An agent told the second will try to repair a part that is fine.
        """
        from app.kernel.errors import OperationNotSupported

        runner = backends.session_for("build")
        runner("catia_new_part", {"name": "Bracket"})
        with pytest.raises(OperationNotSupported):
            runner("catia_measure_part", {})  # a declared tool this backend lacks
