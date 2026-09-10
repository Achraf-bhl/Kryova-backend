"""Which seat serves a conversation, and when none can (E15 task 4).

The claim that matters is not the load balancing — it is the refusal. A CATIA
document is open on one workstation and cannot be reached from another, so
routing around an offline seat is not a fallback, it is a wrong answer that
looks like a working one. Almost every test here is about that.
"""

from __future__ import annotations

from app.catia.affinity import Reason, choose, rebalance_needed


class TestAPinnedConversation:
    def test_it_goes_to_the_seat_holding_its_document(self) -> None:
        outcome = choose(online=["a", "b"], document_device_id="b")

        assert outcome.device_id == "b"
        assert outcome.reason is Reason.PINNED

    def test_an_offline_seat_is_never_routed_around(self) -> None:
        """The failure this module exists to prevent, and it is a silent one:
        another seat fails with "no such document", the agent decides the pad
        failed, and it rebuilds a part that already exists somewhere nobody is
        looking at."""
        outcome = choose(online=["a", "c"], document_device_id="b")

        assert outcome.device_id is None
        assert outcome.reason is Reason.STRANDED

    def test_a_stranded_conversation_names_the_machine_to_start(self) -> None:
        """Collapsing this into "no workstation" sends a user to start the wrong
        machine — the one that is already running."""
        outcome = choose(online=["a"], document_device_id="workstation-7")

        assert outcome.holding_device_id == "workstation-7"
        assert "workstation-7" in outcome.message()
        assert "nothing is lost" in outcome.message()

    def test_stranded_and_nothing_online_are_different_answers(self) -> None:
        """One is solved by connecting any workstation; the other only by
        connecting *that* one."""
        stranded = choose(online=[], document_device_id="b")
        nothing = choose(online=[], document_device_id=None)

        assert stranded.reason is Reason.STRANDED
        assert nothing.reason is Reason.NONE_ONLINE
        assert stranded.message() != nothing.message()

    def test_both_failures_are_falsey(self) -> None:
        assert not choose(online=[], document_device_id="b")
        assert not choose(online=[], document_device_id=None)


class TestAnUnpinnedConversation:
    def test_the_least_loaded_seat_wins(self) -> None:
        """Two seats paired to one user is the ordinary shape of a small team,
        and picking the first in a dictionary means one machine does
        everything."""
        outcome = choose(online=["a", "b"], document_device_id=None, load={"a": 4, "b": 1})

        assert outcome.device_id == "b"
        assert outcome.reason is Reason.BALANCED

    def test_a_seat_with_no_load_recorded_counts_as_idle(self) -> None:
        outcome = choose(online=["a", "b"], document_device_id=None, load={"a": 4})

        assert outcome.device_id == "b"

    def test_a_tie_is_broken_the_same_way_on_every_worker(self) -> None:
        """Two workers picking differently for the same conversation both open a
        document, and then one of them is on the wrong seat forever."""
        first = choose(online=["b", "a"], document_device_id=None)
        second = choose(online=["a", "b"], document_device_id=None)

        assert first.device_id == second.device_id == "a"

    def test_no_seat_online_is_reported_rather_than_guessed(self) -> None:
        outcome = choose(online=[], document_device_id=None)

        assert outcome.device_id is None
        assert outcome.reason is Reason.NONE_ONLINE

    def test_a_duplicate_in_the_online_list_does_not_double_a_seats_chances(self) -> None:
        # A laptop that slept and woke legitimately appears twice in the
        # registry for a moment; that must not make it twice as likely.
        outcome = choose(online=["a", "a", "b"], document_device_id=None, load={"a": 9})

        assert outcome.device_id == "b"


class TestRebalancing:
    def test_an_imbalance_is_reported(self) -> None:
        assert rebalance_needed({"a": 5, "b": 1})

    def test_an_even_spread_is_not(self) -> None:
        assert not rebalance_needed({"a": 3, "b": 2})

    def test_one_seat_is_never_imbalanced_against_itself(self) -> None:
        assert not rebalance_needed({"a": 99})
        assert not rebalance_needed({})

    def test_nothing_here_moves_a_live_conversation(self) -> None:
        """Reported, never acted on: moving a conversation between seats is
        exactly what `choose` refuses to do, so the only honest response to an
        imbalance is to let it drain."""
        import app.catia.affinity as module

        exported = set(module.__all__)

        assert exported == {"Outcome", "Reason", "choose", "rebalance_needed"}
