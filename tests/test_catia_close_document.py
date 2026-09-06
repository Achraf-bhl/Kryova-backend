"""Putting a part away: `catia_close_document` against the daemon.

The complaint this answers is small and concrete -- a day of Kryova work on one
seat leaves an open window per conversation, plus every import, and CATIA's own
window list stops being usable long before its memory does. The agent needs to be
able to close a part it has finished with, without a human clicking anything.

What makes it worth a test file rather than a line of code is that closing is the
one document operation that can *destroy* work, so every assertion here is about
the thing that must not happen:

* the part is written to disk **before** it is dropped, so a change the engineer
  made by hand in CATIA since the last write goes with it;
* only this conversation's own document is closed -- never `ActiveDocument`,
  never the part another conversation has in hand;
* a part that is already closed is *reported*, not raised, and nothing is opened
  in order to be closed;
* and closing is reversible: the file is still there and reopening restores the
  features, the material and the mass.

These drive real frames through `BridgeSession` against the mock backend, the way
`test_document_binding.py` does, because the frame handling is identical on
Windows and the COM leaves are the only part that is not. Every case runs against
an `en` and a `de` mock: the operation must not depend on the seat's language
(it reads no interface text), and pinning that is cheaper than discovering it on
a French seat.

The server-side half -- the binding row, the unscoped frame, the missing
auto-checkpoint -- is in `test_catia_dispatch.py`.

Offline: no database, no network, no CATIA.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import CatiaBackend  # noqa: E402
from catia_bridge.catia_com import CatiaCom  # noqa: E402
from catia_bridge.mock_catia import MockCatia  # noqa: E402
from catia_bridge.session import BridgeSession  # noqa: E402
from catia_bridge.tool_table import TOOLS, WRITE  # noqa: E402

#: Both interface languages every interactive suite here runs against. Closing
#: reads no label and presses no button, so the answer must be identical -- which
#: is exactly the kind of claim that is worth one parametrise rather than a
#: sentence in a docstring.
LANGUAGES = ("en", "de")


@pytest.fixture(params=LANGUAGES)
def mock(request: pytest.FixtureRequest, tmp_path: Path) -> MockCatia:
    return MockCatia(tmp_path / "catia", language=request.param)


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
    """Build a part with a solid in it and return the identity the server sends.

    The same shape `dispatch._enrich` puts in a close call's arguments: the
    conversation's own `doc_name` and `remote_path`, resolved from the binding row
    rather than named by the model.
    """
    created = call(session, "catia_new_part", {"name": name})["data"]
    call(session, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 40, "height_mm": 20})
    call(session, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})
    return {"doc_name": created["doc_name"], "remote_path": created["remote_path"]}


def close(session: BridgeSession, identity: dict | None = None) -> dict:
    result = call(session, "catia_close_document", identity or {})
    assert result["ok"] is True, result.get("error")
    return result["data"]


class TestTheWindowActuallyCloses:
    def test_the_document_is_gone_from_the_open_set(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        """The outcome, not the acknowledgement.

        A backend that returned `{"closed": true}` and left the document in hand
        would pass any test that checked the result alone, and the seat would keep
        filling up with windows while every log line said otherwise.
        """
        bracket = make_part(session, "Bracket")
        assert mock.doc_name == "Bracket"

        data = close(session, bracket)

        assert mock.doc_name is None
        assert mock.doc_path is None
        assert data["closed"] is True
        assert data["saved"] is True
        assert data["open_documents"] == 0

    def test_the_next_operation_finds_nothing_open(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        """Closed means closed, all the way down to the guard every tool runs.

        `_require_document` is what the rest of the backend asks; if closing left
        any part of the document behind, a following pad would succeed and land in
        a part nobody has open.
        """
        bracket = make_part(session, "Bracket")
        close(session, bracket)

        result = call(session, "catia_pad", {"sketch": "Sketch.1", "length_mm": 5})

        assert result["ok"] is False
        assert "No document is open" in result["error"]


class TestNothingIsLost:
    def test_the_part_is_written_before_it_is_dropped(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        """The guard, exercised by putting the backend in the state it guards.

        Every Kryova mutation writes the mock's file as it goes, so through the
        tool layer alone the document is never dirty and this could never fail.
        The change that *is* at risk is the one Kryova did not make: the engineer
        renaming a feature in CATIA between two messages. That state is set up
        directly here -- an in-session edit with no write behind it -- and then the
        window is closed.

        Delete `self._write_document()` from `MockCatia.close_document` and this
        test fails: the reopened part carries the old name, and the engineer's edit
        is gone with no error anywhere. Its COM counterpart is `target.Save()`
        before `target.Close()`, which cannot be exercised off a seat.
        """
        bracket = make_part(session, "Bracket")
        mock.features[-1]["name"] = "HandRenamedByTheEngineer"

        close(session, bracket)
        reopened = call(session, "catia_open_document", bracket)["data"]

        assert "HandRenamedByTheEngineer" in [f["name"] for f in reopened["features"]]

    def test_a_closed_part_reopens_with_its_material_and_its_mass(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        """Closing is putting the part away, not forgetting it.

        This is what makes keeping the conversation's binding row the right answer
        (`test_catia_dispatch.py` covers that end): the file survives the close, so
        there is always a way back to it and nothing about the close needs undoing.
        """
        bracket = make_part(session, "Bracket")
        call(session, "catia_set_material", {"material": "steel-1018", "density_kg_m3": 7870.0})
        before = call(session, "catia_measure", {})["data"]

        close(session, bracket)
        reopened = call(session, "catia_open_document", bracket)["data"]

        assert mock.doc_name == "Bracket"
        assert mock.material == "steel-1018"
        assert reopened["mass_kg"] == pytest.approx(before["mass_kg"])
        assert [f["name"] for f in reopened["features"]] == ["Sketch.1", "Pad.1"]

    def test_a_scoped_call_reopens_the_part_by_itself(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        """The agent does not have to remember it closed anything.

        Every document-scoped call is activated first, and `ensure_document`
        reopens a document CATIA no longer holds. So closing costs the
        conversation nothing beyond the reopen -- which is the property that lets
        the agent close windows freely rather than hoarding them just in case.
        """
        bracket = make_part(session, "Bracket")
        close(session, bracket)

        result = call(
            session, "catia_sketch_circle", {"plane": "XY", "diameter_mm": 8}, document=bracket
        )

        assert result["ok"] is True, result.get("error")
        assert mock.doc_name == "Bracket"
        # And it reopened the real part, not an empty one: the sketch the pad
        # consumed is still there beside the new one.
        assert sorted(mock.sketches) == ["Sketch.1", "Sketch.2"]


class TestItClosesOnlyWhatItWasAsked:
    def test_another_conversations_part_is_left_alone(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        """The failure that would be invisible: closing `ActiveDocument`.

        Two conversations on one seat. The second has `Cover` in hand when a close
        arrives for `Bracket`. A backend that closed whatever CATIA was showing
        would report success and take down the wrong window -- and unlike a pad in
        the wrong part, a closed window leaves nothing in a tree to notice.
        """
        bracket = make_part(session, "Bracket")
        make_part(session, "Cover")
        assert mock.doc_name == "Cover"

        data = close(session, bracket)

        assert data["closed"] is False
        assert mock.doc_name == "Cover"
        assert "not the document in hand" in data["note"]

    def test_a_call_that_names_nothing_closes_nothing(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        """A server that sent no identity must not be read as "close anything".

        The server always resolves the binding row, so this should not arise --
        but "should not" is not a property, and the safe default for a destructive
        direction is to do nothing rather than to guess.
        """
        make_part(session, "Bracket")

        data = close(session)

        assert data["closed"] is False
        assert mock.doc_name == "Bracket"


class TestAlreadyClosedIsNotAFailure:
    def test_closing_a_part_that_is_not_open_is_reported_rather_than_raised(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        """The end state the caller wanted is already true.

        Raising here would send the agent into a recovery loop over a job that is
        done -- and the recovery it would reach for is reopening the part, which
        is the opposite of what was asked.
        """
        bracket = make_part(session, "Bracket")
        close(session, bracket)

        data = close(session, bracket)

        assert data["closed"] is False
        assert data["saved"] is False
        assert data["open_documents"] == 0
        assert "no window to close" in data["note"]

    def test_nothing_is_opened_in_order_to_be_closed(
        self, session: BridgeSession, mock: MockCatia
    ) -> None:
        """The tool exists to reduce the number of open windows, not to add one."""
        bracket = make_part(session, "Bracket")
        close(session, bracket)

        assert close(session, bracket)["open_documents"] == 0
        assert mock.doc_name is None


class TestTheVocabularyAgrees:
    """Structural checks that need no backend, so they run everywhere."""

    def test_the_daemon_knows_the_tool_and_its_tier(self) -> None:
        tier, schema, server_fields = TOOLS["catia_close_document"]
        # WRITE rather than DESTRUCTIVE: the daemon refuses a destructive call
        # that arrives without a server-signed approval token, so getting this
        # wrong would make the tool unreachable rather than dangerous -- but it
        # would be unreachable silently, in front of the user.
        assert tier == WRITE
        # No model-facing arguments at all: the conversation's own document is
        # the only one that can be closed, and the model never names a file.
        assert schema["properties"] == {}
        assert schema["additionalProperties"] is False
        assert server_fields == ("doc_name", "remote_path")

    def test_neither_backend_inherits_a_stub(self) -> None:
        # Closing is abstract on `CatiaBackend`, so this cannot silently regress
        # to a no-op the way an optional hook could. Asserted anyway, because the
        # cost of finding out on a seat is a lost session.
        for backend in (CatiaCom, MockCatia):
            assert "close_document" in vars(backend), (
                f"{backend.__name__} does not define close_document"
            )

    def test_both_backends_take_the_same_arguments(self) -> None:
        """A signature drift here reads as `unimplemented_options` on the seat.

        The daemon calls `close_document(**arguments)` with whatever the server
        put in `server_fields`, so a backend whose signature differs refuses every
        close -- on Windows only, where it is most expensive to discover.
        """

        def shape(function: object) -> list[tuple[str, object]]:
            parameters = inspect.signature(function).parameters  # type: ignore[arg-type]
            return [(name, p.default) for name, p in parameters.items() if name != "self"]

        assert (
            shape(CatiaCom.close_document)
            == shape(MockCatia.close_document)
            == shape(CatiaBackend.close_document)
        )
