"""A conversation owns several CATIA documents, and exactly one is active.

Phase 14, the seat-side half, 2026-09-06. Until this a conversation owned
exactly one document -- a unique constraint said so -- and that was the
mechanic that let the agent say "the part we were working on" without naming
a file. It was also why an assembly could not be built on a seat at all:
ladder prompt S2 (a shaft, a bushing, a clash check) ended with the agent
calling `catia_new_part` seven times at the refusal, because a second part had
nowhere to go.

The mechanic survives as `is_active`. What changes:

* on a seat, a second `catia_new_part` **adds** a row and deactivates the
  current one -- nothing is abandoned, every row keeps its path and its
  checkpoints, and the result says what is still owned;
* the open kernel keeps its one live document and its update-in-place row,
  because that is the contract its eviction recovery depends on;
* `catia_product_create` is bound at last (it never was), as a `product`;
* `catia_open_document name=` switches the active document;
* `catia_component_add kind=existing document=<name>` is resolved to the path
  that part was actually saved under, because `new_part` writes `Bracket-2`
  when `Bracket` is taken and the daemon would otherwise look for the wrong
  file;
* the state block lists the set, because the agent cannot assemble names it
  does not know it holds.

Driven through `dispatch.call_catia` against the scripted device the rest of
the dispatch suite uses, so what is under test is the server's record, not a
mock's memory.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.catia import dispatch
from app.models.catia import CatiaCheckpoint, CatiaDocument
from tests.test_catia_dispatch import run, wired  # noqa: F401 - fixture re-export


def _documents(db_session, conversation_id: str) -> list[CatiaDocument]:
    return list(
        db_session.scalars(
            select(CatiaDocument)
            .where(CatiaDocument.conversation_id == conversation_id)
            .order_by(CatiaDocument.created_at)
        )
    )


def _reply_for(name: str, suffix: str = ".CATPart") -> dict[str, Any]:
    return {"doc_name": name, "remote_path": f"C:\\work\\{name}{suffix}", "features": []}


class TestASecondPartOnASeat:
    def test_it_adds_a_document_and_makes_it_active(self, wired, db_session) -> None:
        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_new_part"] = _reply_for("Housing")
        run(wired, "catia_new_part", {"name": "Housing"})

        rows = _documents(db_session, wired["conversation"].id)
        assert [row.doc_name for row in rows] == ["Bracket", "Housing"]
        assert [row.is_active for row in rows] == [False, True]

    def test_nothing_is_abandoned(self, wired, db_session) -> None:
        """The property `dispatch.py` guards at length for close_document,
        holding here for the same reason: path and checkpoints stay."""
        run(wired, "catia_new_part", {"name": "Bracket"})
        checkpoint_id = run(wired, "catia_checkpoint", {"label": "before"})["checkpoint_id"]
        wired["connection"].replies["catia_new_part"] = _reply_for("Housing")
        run(wired, "catia_new_part", {"name": "Housing"})

        bracket = _documents(db_session, wired["conversation"].id)[0]
        assert bracket.remote_path == "C:\\work\\Bracket.CATPart"
        assert db_session.get(CatiaCheckpoint, checkpoint_id).document_id == bracket.id

    def test_the_result_says_what_is_still_owned(self, wired) -> None:
        """"Started a new part" and "lost the old one" look identical from a
        Done."""
        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_new_part"] = _reply_for("Housing")
        data = run(wired, "catia_new_part", {"name": "Housing"})
        assert "Bracket" in data["note"]
        assert "nothing was discarded" in data["note"]
        assert "catia_open_document" in data["note"]

    def test_the_first_part_says_nothing_about_others(self, wired) -> None:
        data = run(wired, "catia_new_part", {"name": "Bracket"})
        assert "note" not in data

    def test_scoped_calls_go_to_the_active_document(self, wired) -> None:
        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_new_part"] = _reply_for("Housing")
        run(wired, "catia_new_part", {"name": "Housing"})
        wired["connection"].calls.clear()
        run(wired, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})
        scoped = [c for c in wired["connection"].calls if c["tool"] == "catia_pad"][0]
        assert scoped["document"]["doc_name"] == "Housing"


class TestSwitchingByName:
    def _two_parts(self, wired) -> None:
        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_new_part"] = _reply_for("Housing")
        run(wired, "catia_new_part", {"name": "Housing"})

    def test_open_document_with_a_name_makes_it_active(self, wired, db_session) -> None:
        self._two_parts(wired)
        wired["connection"].replies["catia_open_document"] = _reply_for("Bracket")
        run(wired, "catia_open_document", {"name": "Bracket"})
        rows = _documents(db_session, wired["conversation"].id)
        assert {row.doc_name: row.is_active for row in rows} == {"Bracket": True, "Housing": False}

    def test_the_daemon_is_sent_the_named_documents_own_path(self, wired) -> None:
        """The model names documents; the server resolves paths."""
        self._two_parts(wired)
        wired["connection"].replies["catia_open_document"] = _reply_for("Bracket")
        wired["connection"].calls.clear()
        run(wired, "catia_open_document", {"name": "Bracket"})
        sent = wired["connection"].calls[0]["arguments"]
        assert sent["remote_path"] == "C:\\work\\Bracket.CATPart"
        assert sent["doc_name"] == "Bracket"

    def test_the_name_is_matched_without_regard_to_case(self, wired, db_session) -> None:
        self._two_parts(wired)
        wired["connection"].replies["catia_open_document"] = _reply_for("Bracket")
        run(wired, "catia_open_document", {"name": "bracket"})
        rows = _documents(db_session, wired["conversation"].id)
        assert next(r for r in rows if r.doc_name == "Bracket").is_active is True

    def test_a_name_the_conversation_does_not_own_is_refused_with_the_list(
        self, wired
    ) -> None:
        self._two_parts(wired)
        with pytest.raises(dispatch.CatiaError) as raised:
            run(wired, "catia_open_document", {"name": "Flange"})
        message = str(raised.value)
        assert "Flange" in message
        assert "Bracket" in message and "Housing" in message

    def test_without_a_name_the_active_document_is_reopened(self, wired) -> None:
        """The behaviour every existing caller relies on is unchanged."""
        self._two_parts(wired)
        wired["connection"].replies["catia_open_document"] = _reply_for("Housing")
        wired["connection"].calls.clear()
        run(wired, "catia_open_document", {})
        assert wired["connection"].calls[0]["arguments"]["doc_name"] == "Housing"

    def test_there_is_always_exactly_one_active(self, wired, db_session) -> None:
        self._two_parts(wired)
        wired["connection"].replies["catia_open_document"] = _reply_for("Bracket")
        run(wired, "catia_open_document", {"name": "Bracket"})
        run(wired, "catia_open_document", {"name": "Housing"})
        rows = _documents(db_session, wired["conversation"].id)
        assert sum(1 for row in rows if row.is_active) == 1


class TestAProductIsADocumentToo:
    def test_product_create_binds_as_a_product(self, wired, db_session) -> None:
        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_product_create"] = _reply_for(
            "Assembly", ".CATProduct"
        ) | {"part_number": "Assembly", "components": 0}
        data = run(wired, "catia_product_create", {"name": "Assembly"})
        assert data["doc_type"] == "product"
        rows = _documents(db_session, wired["conversation"].id)
        product = next(r for r in rows if r.doc_name == "Assembly")
        assert product.doc_type == "product"
        assert product.is_active is True

    def test_it_needs_no_document_first(self, wired, db_session) -> None:
        wired["connection"].replies["catia_product_create"] = _reply_for(
            "Assembly", ".CATProduct"
        ) | {"part_number": "Assembly", "components": 0}
        run(wired, "catia_product_create", {"name": "Assembly"})
        assert len(_documents(db_session, wired["conversation"].id)) == 1

    def test_it_is_sent_unscoped(self, wired) -> None:
        """Scoping it would activate the part that was current in order to
        start the assembly that replaces it as current."""
        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_product_create"] = _reply_for(
            "Assembly", ".CATProduct"
        ) | {"part_number": "Assembly", "components": 0}
        wired["connection"].calls.clear()
        run(wired, "catia_product_create", {"name": "Assembly"})
        sent = [c for c in wired["connection"].calls if c["tool"] == "catia_product_create"][0]
        assert sent["document"] is None


class TestAddingAnOwnedPartToTheAssembly:
    def _assembly_of_two(self, wired) -> None:
        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_new_part"] = {
            "doc_name": "Housing",
            # What the daemon really does when a name is taken.
            "remote_path": "C:\\work\\Housing-2.CATPart",
            "features": [],
        }
        run(wired, "catia_new_part", {"name": "Housing"})
        wired["connection"].replies["catia_product_create"] = _reply_for(
            "Assembly", ".CATProduct"
        ) | {"part_number": "Assembly", "components": 0}
        run(wired, "catia_product_create", {"name": "Assembly"})
        wired["connection"].calls.clear()

    def test_the_name_becomes_the_path_the_part_was_saved_under(self, wired) -> None:
        self._assembly_of_two(wired)
        run(wired, "catia_component_add", {"kind": "existing", "document": "Housing"})
        sent = [c for c in wired["connection"].calls if c["tool"] == "catia_component_add"][0]
        assert sent["arguments"]["document"] == "C:\\work\\Housing-2.CATPart"

    def test_a_name_that_is_not_ours_goes_through_untouched(self, wired) -> None:
        """The daemon still resolves an open document or a file of its own."""
        self._assembly_of_two(wired)
        run(wired, "catia_component_add", {"kind": "existing", "document": "Bought-in bearing"})
        sent = [c for c in wired["connection"].calls if c["tool"] == "catia_component_add"][0]
        assert sent["arguments"]["document"] == "Bought-in bearing"

    def test_the_call_is_scoped_to_the_product(self, wired) -> None:
        self._assembly_of_two(wired)
        run(wired, "catia_component_add", {"kind": "existing", "document": "Bracket"})
        sent = [c for c in wired["connection"].calls if c["tool"] == "catia_component_add"][0]
        assert sent["document"]["doc_name"] == "Assembly"


class TestTheStatusAndTheStateBlockShowTheSet:
    def test_status_reports_the_active_one_and_the_set(self, wired, db_session) -> None:
        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_new_part"] = _reply_for("Housing")
        run(wired, "catia_new_part", {"name": "Housing"})
        status = run(wired, "catia_status", {})
        assert status["document"]["doc_name"] == "Housing"
        assert [d["doc_name"] for d in status["document"]["owned"]] == ["Bracket", "Housing"]

    def test_the_state_block_lists_them_when_there_are_several(
        self, wired, db_session
    ) -> None:
        from app.ai.state import _catia_lines, owned_documents

        run(wired, "catia_new_part", {"name": "Bracket"})
        wired["connection"].replies["catia_new_part"] = _reply_for("Housing")
        run(wired, "catia_new_part", {"name": "Housing"})
        owned = owned_documents(db_session, wired["conversation"].id)
        assert owned == [("Bracket", "part", False), ("Housing", "part", True)]
        text = "\n".join(_catia_lines(wired["conversation"], True, "Housing", None, owned))
        assert "owns 2 documents" in text
        assert "Bracket (part)" in text and "Housing (part, active)" in text
        assert "catia_product_create" in text

    def test_the_state_block_says_nothing_extra_for_one(self, wired, db_session) -> None:
        from app.ai.state import _catia_lines, owned_documents

        run(wired, "catia_new_part", {"name": "Bracket"})
        owned = owned_documents(db_session, wired["conversation"].id)
        text = "\n".join(_catia_lines(wired["conversation"], True, "Bracket", None, owned))
        assert "catia_documents:" not in text


class TestTheOpenKernelKeepsOneDocument:
    def test_a_second_new_part_updates_the_row_in_place(self, db_session, wired) -> None:
        """`device` None is the kernel: one live document, replaced on
        catia_new_part, and the row updated rather than added -- the contract
        the eviction recovery in tools.py relies on."""
        first = dispatch._bind_document(
            db_session,
            conversation_id=wired["conversation"].id,
            device=None,
            doc_name="Bracket",
            remote_path=None,
            existing=None,
        )
        second = dispatch._bind_document(
            db_session,
            conversation_id=wired["conversation"].id,
            device=None,
            doc_name="Housing",
            remote_path=None,
            existing=first,
        )
        assert second.id == first.id
        assert second.doc_name == "Housing"
        assert len(_documents(db_session, wired["conversation"].id)) == 1


class TestExactlyOneActiveIsEnforcedByTheDatabase:
    def test_two_active_rows_are_refused(self, db_session, wired) -> None:
        """Not an application rule: the partial unique index, so a race
        between two tabs cannot produce two documents every scoped call is
        sent to."""
        from sqlalchemy.exc import IntegrityError

        db_session.add(
            CatiaDocument(conversation_id=wired["conversation"].id, doc_name="A", is_active=True)
        )
        db_session.flush()
        db_session.add(
            CatiaDocument(conversation_id=wired["conversation"].id, doc_name="B", is_active=True)
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_several_inactive_rows_are_fine(self, db_session, wired) -> None:
        for name in ("A", "B", "C"):
            db_session.add(
                CatiaDocument(
                    conversation_id=wired["conversation"].id, doc_name=name, is_active=False
                )
            )
        db_session.flush()


class TestANameAlreadyOwnedIsRefused:
    """Measured on ladder prompt S2 turn 2, 2026-09-06. Turn 1 had built the
    shaft, the bushing and the assembly; turn 2 said "now make the bushing as
    a second part", the model called `catia_new_part name=Bushing`, the daemon
    saved `Bushing-2.CATPart` beside the finished one, and a second assembly
    followed. Nothing was deleted and the user saw the work start over. A name
    this conversation owns means that document.
    """

    def _two_parts(self, wired) -> None:
        wired["connection"].replies["catia_new_part"] = _reply_for("Shaft")
        run(wired, "catia_new_part", {"name": "Shaft"})
        wired["connection"].replies["catia_new_part"] = _reply_for("Bushing")
        run(wired, "catia_new_part", {"name": "Bushing"})
        wired["connection"].calls.clear()

    def test_a_second_part_with_an_owned_name_is_refused_before_the_daemon(
        self, wired, db_session
    ) -> None:
        self._two_parts(wired)
        with pytest.raises(dispatch.CatiaError) as raised:
            run(wired, "catia_new_part", {"name": "Bushing"})
        message = str(raised.value)
        assert "already owns a part called 'Bushing'" in message
        assert "catia_open_document name='Bushing'" in message
        assert not [c for c in wired["connection"].calls if c["tool"] == "catia_new_part"]
        assert len(_documents(db_session, wired["conversation"].id)) == 2

    def test_the_match_ignores_case(self, wired) -> None:
        self._two_parts(wired)
        with pytest.raises(dispatch.CatiaError, match="already owns a part called 'Bushing'"):
            run(wired, "catia_new_part", {"name": "bushing"})

    def test_an_assembly_name_is_protected_too(self, wired) -> None:
        self._two_parts(wired)
        wired["connection"].replies["catia_product_create"] = _reply_for(
            "Assembly", ".CATProduct"
        ) | {"part_number": "Assembly", "components": 0}
        run(wired, "catia_product_create", {"name": "Assembly"})
        with pytest.raises(dispatch.CatiaError, match="already owns an assembly called 'Assembly'"):
            run(wired, "catia_product_create", {"name": "Assembly"})

    def test_a_part_may_not_take_an_assemblys_name_either(self, wired) -> None:
        """One namespace: `catia_open_document name=` could not tell them apart."""
        self._two_parts(wired)
        wired["connection"].replies["catia_product_create"] = _reply_for(
            "Assembly", ".CATProduct"
        ) | {"part_number": "Assembly", "components": 0}
        run(wired, "catia_product_create", {"name": "Assembly"})
        with pytest.raises(dispatch.CatiaError, match="already owns an assembly"):
            run(wired, "catia_new_part", {"name": "Assembly"})

    def test_a_new_name_goes_through(self, wired, db_session) -> None:
        self._two_parts(wired)
        wired["connection"].replies["catia_new_part"] = _reply_for("Housing")
        run(wired, "catia_new_part", {"name": "Housing"})
        assert len(_documents(db_session, wired["conversation"].id)) == 3


class TestTheStateBlocksAnnotationIsTolerated:
    """The state block prints "Assembly (product)" and "Shaft (part, active)";
    on S2 the model copied the annotation into the name and was told the
    conversation owned no such document. It meant the document."""

    def _two_parts(self, wired) -> None:
        wired["connection"].replies["catia_new_part"] = _reply_for("Shaft")
        run(wired, "catia_new_part", {"name": "Shaft"})
        wired["connection"].replies["catia_new_part"] = _reply_for("Bushing")
        run(wired, "catia_new_part", {"name": "Bushing"})

    @pytest.mark.parametrize(
        "spelled", ["Shaft (part)", "Shaft (part, active)", "shaft (PART)", "Shaft  (part)"]
    )
    def test_the_annotation_is_stripped(self, wired, db_session, spelled: str) -> None:
        self._two_parts(wired)
        wired["connection"].replies["catia_open_document"] = _reply_for("Shaft")
        run(wired, "catia_open_document", {"name": spelled})
        active = [d for d in _documents(db_session, wired["conversation"].id) if d.is_active]
        assert [d.doc_name for d in active] == ["Shaft"]

    def test_a_product_annotation_too(self, wired, db_session) -> None:
        self._two_parts(wired)
        wired["connection"].replies["catia_product_create"] = _reply_for(
            "Shaft and bushing assembly", ".CATProduct"
        ) | {"part_number": "Assembly", "components": 0}
        run(wired, "catia_product_create", {"name": "Shaft and bushing assembly"})
        wired["connection"].replies["catia_open_document"] = _reply_for("Shaft")
        run(wired, "catia_open_document", {"name": "Shaft"})
        wired["connection"].replies["catia_open_document"] = _reply_for(
            "Shaft and bushing assembly", ".CATProduct"
        )
        run(wired, "catia_open_document", {"name": "Shaft and bushing assembly (product)"})
        active = [d for d in _documents(db_session, wired["conversation"].id) if d.is_active]
        assert [d.doc_type for d in active] == ["product"]

    def test_parentheses_that_are_part_of_a_name_survive(self, wired, db_session) -> None:
        """Only our annotation is stripped, not any bracket."""
        wired["connection"].replies["catia_new_part"] = _reply_for("Bracket (left)")
        run(wired, "catia_new_part", {"name": "Bracket (left)"})
        wired["connection"].replies["catia_new_part"] = _reply_for("Bracket (right)")
        run(wired, "catia_new_part", {"name": "Bracket (right)"})
        wired["connection"].replies["catia_open_document"] = _reply_for("Bracket (left)")
        run(wired, "catia_open_document", {"name": "Bracket (left)"})
        active = [d for d in _documents(db_session, wired["conversation"].id) if d.is_active]
        assert [d.doc_name for d in active] == ["Bracket (left)"]

