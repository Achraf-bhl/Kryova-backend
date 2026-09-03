"""A conversation acts on the document it owns, not on whatever CATIA is showing.

Every operation used to run against `ActiveDocument`, which is a property of the
*screen* rather than of the conversation. Two ways that goes wrong, and neither
of them fails loudly:

* The engineer clicks another part between two messages -- or opens a second
  Kryova conversation in another tab -- and the next pad lands in a part nobody
  was discussing. Nothing errors. The wrong file just grows a feature.
* CATIA is closed and reopened overnight. The conversation resumes, the model
  does not think to reopen anything, and the first modelling call fails with
  "no document is open" -- or worse, succeeds against whatever the engineer
  happened to open that morning.

So the server now names the document on every scoped call and the daemon
activates it first. These drive the daemon the same way `test_catia_daemon.py`
does -- real frames through `BridgeSession`, against the mock backend -- because
the point is the frame handling, and that is identical on Windows.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import OUT_OF_BAND_TOOLS, CatiaBackend  # noqa: E402
from catia_bridge.catia_com import CatiaCom  # noqa: E402
from catia_bridge.mock_catia import MockCatia  # noqa: E402
from catia_bridge.session import BridgeSession  # noqa: E402


@pytest.fixture
def mock(tmp_path: Path) -> MockCatia:
    return MockCatia(tmp_path / "catia")


@pytest.fixture
def session(mock: MockCatia) -> BridgeSession:
    sent: list[dict] = []
    bridge = BridgeSession(mock, bridge_version="1.0.0", hostname="WS-TEST", send=sent.append)
    bridge.sent = sent  # type: ignore[attr-defined]
    return bridge


def call(session: BridgeSession, tool: str, arguments: dict | None = None, **extra) -> dict:
    identifier = f"call-{len(session.sent)}"
    session.handle_frame(
        json.dumps(
            {
                "type": "call",
                "id": identifier,
                "tool": tool,
                "conversation_id": "conv-1",
                "arguments": arguments or {},
                **extra,
            }
        )
    )
    return next(f for f in reversed(session.sent) if f.get("id") == identifier)


def make_part(session: BridgeSession, name: str) -> dict:
    """Create a part, pad it, and return the document envelope the server would send."""
    created = call(session, "catia_new_part", {"name": name})["data"]
    call(session, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 40, "height_mm": 20})
    call(session, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})
    return {"doc_name": created["doc_name"], "remote_path": created["remote_path"]}


class TestReattach:
    def test_a_scoped_call_runs_against_the_document_it_names(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        # The failure this exists for. Two parts, the second one in hand, and a
        # call arrives for the first. Without the envelope the pocket lands in
        # `Cover`; with it, `Bracket` is reopened and `Cover` is untouched.
        bracket = make_part(session, "Bracket")
        cover = make_part(session, "Cover")
        assert mock.doc_name == "Cover"

        result = call(
            session,
            "catia_sketch_circle",
            {"plane": "XY", "diameter_mm": 8},
            document=bracket,
        )

        assert result["ok"] is True, result.get("error")
        assert mock.doc_name == "Bracket"
        assert len(mock.sketches) == 2

        # And `Cover` really was left alone -- proved by reopening it rather
        # than by trusting the counter, since a mock that mislaid the switch
        # would report whatever it had in hand.
        call(session, "catia_open_document", cover)
        assert len(mock.sketches) == 1

    def test_the_document_already_in_hand_is_not_reloaded(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        # The common case, and the one that has to stay cheap: this runs before
        # every single operation. A reload would also silently discard anything
        # not yet written to the file.
        bracket = make_part(session, "Bracket")
        mock.selection = ["Pad.1"]

        assert call(session, "catia_measure", {}, document=bracket)["ok"] is True

        assert mock.selection == ["Pad.1"]

    def test_a_document_that_left_the_workstation_is_refused_by_name(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        # A cleaned temp directory or a reimaged laptop. Nothing here can fix
        # it, and modelling into the part that happens to be on screen instead
        # would be the worst available answer -- so it refuses, and names the
        # one tool that carries the checkpoint bytes to rebuild from.
        bracket = make_part(session, "Bracket")
        make_part(session, "Cover")
        Path(bracket["remote_path"]).unlink()

        result = call(session, "catia_pocket", {"sketch": "Sketch.1", "depth_mm": 2}, document=bracket)

        assert result["ok"] is False
        assert "catia_open_document" in result["error"]
        assert "Bracket" in result["error"]

    def test_a_reopened_part_still_weighs_what_it_weighed(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        # The material is applied to the document in CATIA, so it has to survive
        # a reopen here too. It did not: closing and reopening a steel part
        # reverted it to the default density, and every mass reported afterwards
        # was wrong by the ratio of the two -- silently, because a mass is a
        # plausible number whatever it is.
        bracket = make_part(session, "Bracket")
        call(
            session,
            "catia_set_material",
            {"material": "steel-1018", "density_kg_m3": 7870},
            document=bracket,
        )
        steel = call(session, "catia_measure", {}, document=bracket)["data"]["mass_kg"]

        make_part(session, "Cover")
        reopened = call(session, "catia_measure", {}, document=bracket)["data"]

        assert reopened["mass_kg"] == pytest.approx(steel)
        assert mock.material == "steel-1018"


class TestScoping:
    def test_a_call_with_no_document_behaves_as_it_always_did(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        # An older server, or one of the tools the server deliberately leaves
        # unscoped. "No field" must mean "not scoped", never "scoped to
        # nothing" -- the second would break every unscoped tool at once.
        make_part(session, "Bracket")
        assert call(session, "catia_measure", {})["ok"] is True

    @pytest.mark.parametrize(
        "document",
        ["Bracket", {"remote_path": "C:\\work\\Bracket.CATPart"}, {"doc_name": ""}, None],
        ids=["a-bare-string", "no-name", "an-empty-name", "null"],
    )
    def test_a_malformed_envelope_is_ignored_rather_than_obeyed(
        self, session: BridgeSession, mock: MockCatia, document: object
    ) -> None:
        # Read defensively for the same reason the tier is taken from the
        # daemon's own table and never from the frame. A field that only ever
        # *narrows* what a call may touch has one honest failure mode when it
        # arrives broken: behave as though it were absent.
        make_part(session, "Bracket")
        assert call(session, "catia_measure", {}, document=document)["ok"] is True

    def test_the_interactive_tools_are_never_scoped(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        # These run precisely when a modal dialog has COM blocked, and
        # activating a document is a COM call. Scoping them would mean the tools
        # whose job is to clear a stuck dialog could only run when no dialog was
        # stuck. Sent a document for a part that does not exist, they must still
        # run -- which is only true if they never look at it.
        make_part(session, "Bracket")
        ghost = {"doc_name": "Ghost", "remote_path": "C:\\nowhere\\Ghost.CATPart"}

        for tool in sorted(OUT_OF_BAND_TOOLS & {"catia_describe_dialog", "catia_list_commands"}):
            result = call(session, tool, {}, document=ghost)
            assert "not open" not in (result.get("error") or ""), tool


class TestBothBackendsAnswerTheQuestion:
    def test_neither_backend_inherits_the_no_op(self) -> None:
        # `CatiaBackend.ensure_document` defaults to doing nothing, which is
        # right only for a backend that cannot hold a second document. Both real
        # ones can, so inheriting the default would mean silently acting on the
        # wrong part -- the exact bug this file is about, reintroduced by
        # omission rather than by a decision.
        for backend in (CatiaCom, MockCatia):
            assert backend.ensure_document is not CatiaBackend.ensure_document, (
                f"{backend.__name__} inherits the no-op ensure_document. It holds "
                "documents, so it must override it or every operation acts on "
                "whatever CATIA had active."
            )

    def test_both_take_the_same_arguments(self) -> None:
        # The daemon calls this by keyword off the frame, so a backend whose
        # signature drifted would raise TypeError on Windows only. Compared on
        # parameter names and kinds rather than on the whole `Signature`:
        # `catia_com` has `from __future__ import annotations` and `backend`
        # does not, so the same annotation is a string in one and a type in the
        # other, and equality is False for a reason that could not matter less.
        def shape(function: object) -> list[tuple[str, object]]:
            return [
                (name, parameter.kind)
                for name, parameter in inspect.signature(function).parameters.items()
            ]

        base = shape(CatiaBackend.ensure_document)
        for backend in (CatiaCom, MockCatia):
            assert shape(backend.ensure_document) == base, backend.__name__
