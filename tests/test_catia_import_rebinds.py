"""After `catia_import` the conversation works on what it imported (ROAD_TO_10 5.8).

The daemon opens an imported file as a *new* document, so CATIA's active window became the
import while the conversation's binding row still named the part it had before. The next
scoped call reattached to the old part and the import sat open beside it -- coherent, and no
use to anyone who imported a supplier's STEP in order to model on it. `dispatch.py` carried a
comment saying the question was open; this is its answer.

Driven through `call_catia` against the scripted device, **not** through the dispatcher's
private helpers: CLAUDE.md *Testing* 8 -- a test that calls a function directly proves the
function and not the path the agent is offered. The daemon half (the file becomes a saved
document with a path) runs the real frame handling against the mock backend.

Offline: nothing here needs CATIA. What it cannot say is whether `SaveAs` on a freshly imported
STEP document behaves on a seat; that is THE QUEUE G8.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.catia import dispatch
from app.models import MediaKind, Project
from app.models.catia import CatiaDocument
from app.models.geometry import GeometryVersion
from app.models.media import Media
from tests.test_catia_dispatch import run, wired  # noqa: F401 - fixture re-export

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.mock_catia import MockCatia  # noqa: E402
from catia_bridge.session import BridgeSession  # noqa: E402

STEP_BYTES = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


def _documents(db_session, conversation_id: str) -> list[CatiaDocument]:
    return list(
        db_session.scalars(
            select(CatiaDocument)
            .where(CatiaDocument.conversation_id == conversation_id)
            .order_by(CatiaDocument.created_at)
        )
    )


@pytest.fixture
def uploaded(wired, media_store, db_session) -> str:
    """A project holding `supplier.stp`, with the conversation attached to it."""
    blob = media_store.write_bytes(STEP_BYTES)
    project = Project(name="Import", owner_id=wired["user_id"])
    db_session.add(project)
    db_session.flush()
    media = Media(
        owner_id=wired["user_id"],
        kind=MediaKind.CAD,
        filename="supplier.stp",
        content_type="model/step",
        size_bytes=blob.size_bytes,
        sha256=blob.digest,
    )
    db_session.add(media)
    db_session.flush()
    db_session.add(
        GeometryVersion(
            project_id=project.id,
            media_id=media.id,
            version_number=1,
            filename="supplier.stp",
            file_format="step",
        )
    )
    wired["conversation"].project_id = project.id
    db_session.commit()
    return "supplier.stp"


def _imported(name: str = "supplier", **extra: Any) -> dict[str, Any]:
    return {
        "file": "supplier.stp",
        "format": "step",
        "document": f"{name}.CATPart",
        "doc_name": name,
        "remote_path": f"C:\\work\\{name}.CATPart",
        "doc_type": "part",
        "solids": 1,
        **extra,
    }


class TestTheImportBecomesTheActiveDocument:
    def test_the_conversation_is_rebound_and_the_old_part_is_kept(
        self, wired, db_session, uploaded
    ) -> None:
        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_import"] = _imported()

        result = run(wired, "catia_import", {"file": uploaded})

        rows = _documents(db_session, wired["conversation"].id)
        assert [row.doc_name for row in rows] == ["Bracket", "supplier"]
        assert [row.is_active for row in rows] == [False, True]
        assert rows[1].remote_path == "C:\\work\\supplier.CATPart"
        assert result["bound"] is True
        assert result["document_id"] == rows[1].id
        # Said in the result the model reads, because "imported" and "the old part is
        # gone" look identical from a `Done`.
        assert "Bracket" in result["note"] and "nothing was discarded" in result["note"]

    def test_the_next_call_is_scoped_to_the_imported_document(
        self, wired, db_session, uploaded
    ) -> None:
        # The behaviour the rebinding is *for*: before it, this call carried Bracket.
        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_import"] = _imported()
        run(wired, "catia_import", {"file": uploaded})

        run(wired, "catia_measure", {})

        envelope = wired["connection"].calls[-1]["document"]
        assert envelope["doc_name"] == "supplier"
        assert envelope["remote_path"] == "C:\\work\\supplier.CATPart"

    def test_an_import_with_no_part_yet_becomes_the_first_document(
        self, wired, db_session, uploaded
    ) -> None:
        wired["connection"].replies["catia_import"] = _imported()

        result = run(wired, "catia_import", {"file": uploaded})

        rows = _documents(db_session, wired["conversation"].id)
        assert [(row.doc_name, row.is_active) for row in rows] == [("supplier", True)]
        assert result["bound"] is True
        assert "note" not in result  # nothing was displaced, so nothing to explain

    def test_an_assembly_import_is_recorded_as_a_product(
        self, wired, db_session, uploaded
    ) -> None:
        wired["connection"].replies["catia_import"] = _imported(doc_type="product")

        run(wired, "catia_import", {"file": uploaded})

        assert _documents(db_session, wired["conversation"].id)[0].doc_type == "product"


class TestAnImportThatCannotBeFoundAgainIsNotBound:
    def test_no_reported_path_leaves_the_binding_where_it_was(
        self, wired, db_session, uploaded
    ) -> None:
        # A binding with no path cannot be reopened after CATIA restarts, so binding on
        # a daemon that did not say where it saved the document would be a promise the
        # system cannot keep. The honest answer is "open, and not bound".
        run(wired, "catia_new_part", {"name": "Bracket"})
        reply = _imported()
        del reply["remote_path"]
        wired["connection"].replies["catia_import"] = reply

        result = run(wired, "catia_import", {"file": uploaded})

        rows = _documents(db_session, wired["conversation"].id)
        assert [(row.doc_name, row.is_active) for row in rows] == [("Bracket", True)]
        assert result["bound"] is False
        assert "Bracket" in result["note"] and "not rebound" in result["note"]

    def test_an_unnamed_document_is_not_bound_either(
        self, wired, db_session, uploaded
    ) -> None:
        run(wired, "catia_new_part", {"name": "Bracket"})
        reply = _imported()
        del reply["doc_name"]
        wired["connection"].replies["catia_import"] = reply

        assert run(wired, "catia_import", {"file": uploaded})["bound"] is False
        assert len(_documents(db_session, wired["conversation"].id)) == 1


class TestTheImportCallStaysUnscoped:
    def test_the_import_itself_does_not_activate_the_old_part_first(
        self, wired, db_session, uploaded
    ) -> None:
        # Rebinding afterwards is not a reason to scope the call: activating a part in
        # order to open another beside it is work done for nothing, and `_UNSCOPED_TOOLS`
        # says so.
        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_import"] = _imported()

        run(wired, "catia_import", {"file": uploaded})

        import_call = next(c for c in wired["connection"].calls if c["tool"] == "catia_import")
        assert import_call["document"] is None
        assert "catia_import" in dispatch._UNSCOPED_TOOLS


class TestTheDaemonSavesWhatItImported:
    def _session(self, tmp_path: Path) -> tuple[MockCatia, BridgeSession]:
        mock = MockCatia(tmp_path / "catia")
        sent: list[dict] = []
        session = BridgeSession(mock, bridge_version="1.0.0", hostname="WS", send=sent.append)
        session.sent = sent  # type: ignore[attr-defined]
        return mock, session

    def _call(self, session: BridgeSession, arguments: dict, **extra: Any) -> dict:
        identifier = f"call-{len(session.sent)}"  # type: ignore[attr-defined]
        session.handle_frame(
            json.dumps(
                {
                    "type": "call",
                    "id": identifier,
                    "tool": "catia_import",
                    "conversation_id": "conv-1",
                    "arguments": arguments,
                    **extra,
                }
            )
        )
        return next(f for f in reversed(session.sent) if f.get("id") == identifier)  # type: ignore[attr-defined]

    def test_the_imported_document_has_a_path_the_server_can_bind(self, tmp_path: Path) -> None:
        import base64

        mock, session = self._session(tmp_path)
        reply = self._call(
            session,
            {
                "file": "supplier.stp",
                "filename": "supplier.stp",
                "content_b64": base64.b64encode(STEP_BYTES).decode(),
                "content_hash": hashlib.sha256(STEP_BYTES).hexdigest(),
            },
        )

        assert reply["ok"] is True, reply.get("error")
        data = reply["data"]
        assert Path(data["remote_path"]).is_file()
        assert data["doc_name"] == "supplier"
        # And it is the document the daemon now holds, by the rule `ensure_document` uses.
        assert mock.ensure_document(doc_name="supplier", remote_path=data["remote_path"]) is False

    def test_a_file_that_arrives_damaged_is_refused_not_imported(self, tmp_path: Path) -> None:
        import base64

        _mock, session = self._session(tmp_path)
        reply = self._call(
            session,
            {
                "file": "supplier.stp",
                "content_b64": base64.b64encode(STEP_BYTES).decode(),
                "content_hash": "0" * 64,
            },
        )

        assert reply["ok"] is False
        assert "intact" in json.dumps(reply)
