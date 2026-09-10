"""Escalating with a specific question when a bounded retry runs out (E16 task 4).

Three of the four parts of task 4 already existed and are behavioural: the agent
refuses a read repeated verbatim, refuses a write already refused, and refuses
opening empty documents as though it were progress. Those bound the retrying.
What was missing is the last word — and the difference between an escalation and
"the agent kept repeating itself" is that the first one names a decision the
user can make.

So the claims here are about the *sentence*: that it has a subject, a cause and a
question; that a failure nothing recognises still escalates, quoting verbatim;
and that a turn which went fine escalates nothing at all.
"""

from __future__ import annotations

from app.ai.recovery import (
    BAD_ARGUMENT,
    GEOMETRY,
    MAX_SAME_FAILURE,
    MISSING_SUBJECT,
    REFUSED,
    UNAVAILABLE,
    UNCLASSIFIED,
    Failure,
    Recovery,
    escalate,
)


class TestClassification:
    def test_a_missing_thing_is_a_missing_subject(self) -> None:
        assert Failure("catia_pad", "No feature called 'plate.body'.").kind == MISSING_SUBJECT

    def test_an_unreachable_seat_is_not_a_bad_argument(self) -> None:
        """The split is by who has to decide. "The seat is not there" and "the
        argument is wrong" need different people."""
        assert Failure("catia_status", "The workstation is not reachable.").kind == UNAVAILABLE

    def test_a_refusal_is_its_own_kind(self) -> None:
        failure = Failure("run_simulation", "The mesh has 800,000 elements, over the limit.")

        assert failure.kind == REFUSED

    def test_geometry_that_will_not_take_an_operation_is_its_own_kind(self) -> None:
        failure = Failure("catia_pad", "Cannot be padded: the profile is an open sketch.")

        assert failure.kind == GEOMETRY

    def test_a_wrong_argument_is_a_bad_argument(self) -> None:
        assert Failure("catia_fillet", "radius_mm must be positive.").kind == BAD_ARGUMENT

    def test_something_it_does_not_recognise_says_so_rather_than_guessing(self) -> None:
        """The contract `diagnose.py` holds: a taxonomy that labelled everything
        would destroy the evidence the next pattern is written from."""
        failure = Failure("catia_pad", "Erreur 0x80004005")

        assert failure.kind == UNCLASSIFIED
        assert not failure.classified


class TestTheSubject:
    def test_the_subject_comes_from_the_arguments(self) -> None:
        """"The pad failed" is not answerable; "the pad at plate.body failed"
        is."""
        failure = Failure("catia_pad", "Cannot be padded.", {"sketch": "plate.profile"})

        assert failure.subject == "plate.profile"

    def test_arguments_that_name_nothing_give_no_subject_rather_than_an_invented_one(
        self,
    ) -> None:
        failure = Failure("catia_pad", "Cannot be padded.", {"length_mm": 8})

        assert failure.subject == ""


class TestTheBound:
    def test_nothing_escalates_before_the_bound(self) -> None:
        recovery = Recovery()
        for _ in range(MAX_SAME_FAILURE - 1):
            recovery.record(Failure("catia_pad", "No feature called 'x'."))

        assert recovery.exhausted() is None
        assert recovery.escalation() == ""

    def test_a_turn_that_went_fine_escalates_nothing(self) -> None:
        # The difference between an escalation and a nag.
        assert Recovery().escalation() == ""

    def test_the_same_failure_worded_differently_still_counts_as_the_same(self) -> None:
        """A counter keyed on the exact message would never reach its bound,
        which is how a retry budget quietly stops existing."""
        recovery = Recovery()
        recovery.record(Failure("catia_pad", "No feature called 'a'."))
        recovery.record(Failure("catia_pad", "No such feature: 'a'"))
        recovery.record(Failure("catia_pad", "'a' does not exist."))

        assert recovery.exhausted() == ("catia_pad", MISSING_SUBJECT)

    def test_three_different_tools_failing_once_each_do_not_escalate(self) -> None:
        recovery = Recovery()
        for tool in ("catia_pad", "catia_fillet", "catia_pocket"):
            recovery.record(Failure(tool, "No feature called 'x'."))

        assert recovery.exhausted() is None


class TestTheQuestion:
    def _exhausted(self, failure: Failure) -> str:
        return escalate([failure] * MAX_SAME_FAILURE)

    def test_it_names_the_tool_the_subject_the_cause_and_a_decision(self) -> None:
        sentence = self._exhausted(
            Failure("catia_pad", "Cannot be padded: open profile.", {"sketch": "plate.profile"})
        )

        assert "catia_pad" in sentence
        assert "plate.profile" in sentence
        assert "will not take" in sentence
        assert sentence.rstrip().endswith("?")

    def test_it_quotes_the_tools_own_words(self) -> None:
        """The exact wording is the evidence. A paraphrase between the user and
        the failure is what an LLM would have added, on the one screen where it
        must not."""
        sentence = self._exhausted(Failure("catia_pad", "Cannot be padded: open profile."))

        assert "Cannot be padded: open profile." in sentence

    def test_an_unrecognised_failure_escalates_quoting_verbatim(self) -> None:
        sentence = self._exhausted(Failure("catia_pad", "Erreur 0x80004005"))

        assert "Erreur 0x80004005" in sentence
        assert "do not recognise" in sentence

    def test_a_long_message_is_truncated_visibly(self) -> None:
        """A quote cut off with no mark reads as the whole thing, and the point
        of quoting verbatim is that the quote can be trusted."""
        sentence = self._exhausted(Failure("catia_pad", "No such feature. " + "x" * 500))

        assert "…" in sentence

    def test_each_kind_asks_about_something_the_user_can_decide(self) -> None:
        """The thing that stops an escalation being a restatement of the error."""
        for message in (
            "No feature called 'x'.",
            "must be positive",
            "not reachable",
            "refused",
            "open profile",
            "Erreur 0x80004005",
        ):
            sentence = self._exhausted(Failure("catia_pad", message))
            assert "?" in sentence, message
            assert len(sentence) > 60, message

    def test_the_question_describes_the_latest_attempt_not_the_first(self) -> None:
        """The latest message describes the state the part is actually in; the
        first may describe a problem that has since moved."""
        recovery = Recovery()
        recovery.record(Failure("catia_pad", "No feature called 'old'."))
        recovery.record(Failure("catia_pad", "No feature called 'mid'."))
        recovery.record(Failure("catia_pad", "No feature called 'new'."))

        assert "new" in recovery.escalation()
        assert "old" not in recovery.escalation()


class TestRepairs:
    def test_every_kind_has_something_to_try(self) -> None:
        # A repair suggestion nobody wrote is a repair that does not happen.
        for message, kind in (
            ("No feature called 'x'.", MISSING_SUBJECT),
            ("must be positive", BAD_ARGUMENT),
            ("not reachable", UNAVAILABLE),
            ("refused", REFUSED),
            ("open profile", GEOMETRY),
            ("Erreur 0x80004005", UNCLASSIFIED),
        ):
            failure = Failure("catia_pad", message)
            assert failure.kind == kind
            assert failure.repair()

    def test_an_unavailable_thing_is_not_told_to_retry(self) -> None:
        # Retrying an unreachable workstation is the loop the bound exists for.
        assert "not retry" in Failure("catia_status", "not reachable").repair()

    def test_a_refusal_is_told_it_will_not_succeed_by_repetition(self) -> None:
        assert "repeated" in Failure("run_simulation", "over the limit").repair()
