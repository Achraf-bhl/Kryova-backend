"""`call_catia` routes by seat affinity (ROAD_TO_10 5.9, E15 task 4).

`affinity.py` decided this correctly and was imported by nothing but its own test:
`dispatch._online` took the first device online. With two workstations paired to one user a
conversation whose part was open on the second was sent to the first -- which fails as "no
such document", or succeeds against a part of the same name. These drive the real dispatcher
against two scripted seats; `test_catia_affinity.py` keeps proving the pure rule.

The claim that matters is the refusal, as in that file: **a document whose seat is offline is
stranded, never rerouted** (CLAUDE.md, *Do not* 14), and the proof is that the other seat
received no call at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.catia import dispatch
from app.catia.connection import registry
from app.catia.dispatch import CatiaUnavailable
from app.models import Conversation
from app.models.catia import CatiaDevice, CatiaDeviceStatus, CatiaDocument
from tests.test_catia_dispatch import ScriptedDevice, run, wired  # noqa: F401 - fixture re-export


@pytest.fixture
def second_seat(wired, db_session):  # noqa: F811
    """A second paired workstation, online, named so a message can be read."""
    device = CatiaDevice(
        owner_id=wired["user_id"],
        name="Lab laptop",
        status=CatiaDeviceStatus.ACTIVE,
        token_hash="1" * 64,
    )
    db_session.add(device)
    db_session.commit()
    connection = ScriptedDevice(device.id, wired["user_id"])
    registry.register(connection)
    try:
        yield {"device": device, "connection": connection}
    finally:
        registry.unregister(connection)


def _hold(db_session, conversation: Conversation, device: CatiaDevice, name: str = "Bracket"):
    row = CatiaDocument(
        conversation_id=conversation.id,
        device_id=device.id,
        doc_name=name,
        remote_path=f"C:\\work\\{name}.CATPart",
        doc_type="part",
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


class TestAConversationGoesToTheSeatHoldingItsDocument:
    def test_a_pinned_conversation_is_not_sent_to_the_first_seat_online(
        self, wired, second_seat, db_session
    ) -> None:
        _hold(db_session, wired["conversation"], second_seat["device"])

        run(wired, "catia_measure", {})

        assert second_seat["connection"].tools_called != []
        # The first seat -- the one `_online` used to return -- was never called.
        assert wired["connection"].calls == []

    def test_the_document_envelope_goes_to_that_seat_too(
        self, wired, second_seat, db_session
    ) -> None:
        _hold(db_session, wired["conversation"], second_seat["device"], "Cover")

        run(wired, "catia_measure", {})

        assert second_seat["connection"].calls[-1]["document"]["doc_name"] == "Cover"


class TestAnOfflineSeatIsNeverRoutedAround:
    def test_a_stranded_conversation_is_refused_and_no_other_seat_is_called(
        self, wired, second_seat, db_session
    ) -> None:
        _hold(db_session, wired["conversation"], second_seat["device"])
        second_seat["connection"].close()

        with pytest.raises(CatiaUnavailable):
            run(wired, "catia_measure", {})

        # The failure this exists to prevent, and a silent one: the other seat answers
        # "no such document", the agent decides the pad failed and rebuilds a part that
        # already exists on a machine nobody is looking at.
        assert wired["connection"].calls == []

    def test_the_refusal_names_the_machine_to_start_and_not_an_id(
        self, wired, second_seat, db_session
    ) -> None:
        _hold(db_session, wired["conversation"], second_seat["device"])
        second_seat["connection"].close()

        with pytest.raises(CatiaUnavailable) as raised:
            run(wired, "catia_measure", {})

        message = str(raised.value)
        assert "Lab laptop" in message
        assert second_seat["device"].id not in message
        assert "nothing is lost" in message

    def test_a_seat_that_was_revoked_does_not_strand_the_conversation_for_good(
        self, wired, second_seat, db_session
    ) -> None:
        # Waiting for a machine that can never reconnect is not a policy. The way back is
        # the daemon's `ensure_document` refusal naming `catia_open_document`, which
        # restores from the checkpoint the server kept.
        _hold(db_session, wired["conversation"], second_seat["device"])
        second_seat["device"].status = CatiaDeviceStatus.REVOKED
        db_session.commit()

        run(wired, "catia_measure", {})

        assert wired["connection"].tools_called != []

    def test_reopening_on_another_seat_rehomes_the_document(
        self, wired, second_seat, db_session
    ) -> None:
        row = _hold(db_session, wired["conversation"], second_seat["device"])
        second_seat["device"].status = CatiaDeviceStatus.REVOKED
        db_session.commit()

        run(wired, "catia_open_document", {})

        db_session.refresh(row)
        assert row.device_id == wired["device"].id


class TestAnUnpinnedConversationGoesToTheQuietSeat:
    def test_the_least_loaded_online_seat_is_chosen(
        self, wired, second_seat, db_session
    ) -> None:
        # Another conversation already has a part open on one seat. The busy one is the seat
        # with the *smaller* id, because an idle tie is broken by id (`affinity.choose`): a
        # router that ignored load would then pick the busy seat every time, instead of
        # passing by luck on half the runs.
        seats = sorted(
            [(wired["device"], wired["connection"]), (second_seat["device"], second_seat["connection"])],
            key=lambda pair: pair[0].id,
        )
        (busy_device, busy_connection), (quiet_device, quiet_connection) = seats
        busy = Conversation(owner_id=wired["user_id"], title="Busy")
        db_session.add(busy)
        db_session.commit()
        _hold(db_session, busy, busy_device)

        run(wired, "catia_new_part", {"name": "Housing"})

        assert quiet_connection.tools_called != []
        assert busy_connection.calls == []
        row = db_session.scalar(
            select(CatiaDocument).where(CatiaDocument.conversation_id == wired["conversation"].id)
        )
        assert row is not None and row.device_id == quiet_device.id

    def test_a_single_seat_is_still_just_used(self, wired, db_session) -> None:
        run(wired, "catia_new_part", {"name": "Bracket"})

        assert wired["connection"].tools_called != []


def test_the_dispatcher_imports_the_affinity_rule() -> None:
    """The module was imported by its own test only for weeks; this is the tripwire."""
    assert dispatch.affinity.choose is not None
