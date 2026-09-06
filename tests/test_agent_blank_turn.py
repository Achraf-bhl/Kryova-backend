"""A turn that ends with no text must not deny work it actually did.

Measured on the seat 2026-09-06, ladder prompt H2 -- a flange defined entirely
by ratios. The agent got it exactly right: bore 40, outer diameter 3x that,
thickness an eighth of the outer diameter, an 8-hole bolt circle halfway
between. Twelve steps, `Extrusion.1`, eight holes, a final measure of
0.141372 kg, and the volume agrees with pi/4 x (120^2 - 40^2) x 15 minus eight
10 mm bores to the cubic millimetre.

Then the model returned an empty final message twice, the correction budget ran
out, and the user was shown:

    I did not manage to produce an answer for that. Try asking again, or more
    specifically.

Every word of which is wrong about the part. A user who believes it asks again
and ends up with two flanges and no way to tell which is which; a user who
believes it and gives up has thrown away a correct part.

Writing up the result and doing the work are separate, and only one of them
failed. So the message now depends on whether any tool actually succeeded, and
names what did -- listed rather than summarised, because summarising is
precisely what the model just failed at, and repeating the step labels is
something the code can be certain is true.

Offline: no provider, no database, no seat.
"""

from __future__ import annotations

from app.ai.agent import AgentStep, _blank_turn_message

LABELS = {
    "catia_new_part": "Creating a CATIA part",
    "catia_pad": "CATIA: pad",
    "catia_hole_pattern": "CATIA: hole pattern",
    "catia_measure": "Measuring the part",
}


def step(tool: str, *, ok: bool = True) -> AgentStep:
    return AgentStep(tool=tool, arguments={}, ok=ok, result={})


class TestNothingRan:
    """The original message is right for the case it was written for."""

    def test_no_steps_at_all(self) -> None:
        message = _blank_turn_message([], LABELS)
        assert "did not manage to produce an answer" in message
        assert "nothing has changed" in message

    def test_every_step_failed(self) -> None:
        """A refused call changed nothing, so nothing is still the truth."""
        steps = [step("catia_pad", ok=False), step("catia_hole_pattern", ok=False)]
        message = _blank_turn_message(steps, LABELS)
        assert "Nothing was run, so nothing has changed" in message


class TestWorkWasDone:
    """The H2 case, and the one the old message lied about."""

    def test_it_does_not_claim_nothing_was_produced(self) -> None:
        steps = [step("catia_new_part"), step("catia_pad"), step("catia_measure")]
        message = _blank_turn_message(steps, LABELS)
        assert "did not manage to produce an answer" not in message
        assert "nothing has changed" not in message

    def test_it_says_the_work_ran_and_the_write_up_did_not(self) -> None:
        message = _blank_turn_message([step("catia_pad")], LABELS)
        assert "ran" in message
        assert "did not manage to write up the result" in message

    def test_it_tells_the_user_not_to_ask_again(self) -> None:
        """The actionable half. Asking again is what produces a second part."""
        message = _blank_turn_message([step("catia_pad")], LABELS)
        assert "Nothing needs redoing" in message

    def test_it_names_what_was_done(self) -> None:
        steps = [step("catia_new_part"), step("catia_pad"), step("catia_measure")]
        message = _blank_turn_message(steps, LABELS)
        for label in ("Creating a CATIA part", "CATIA: pad", "Measuring the part"):
            assert f"- {label}" in message

    def test_a_failed_step_is_not_listed_as_work_done(self) -> None:
        """Listing a refusal as an accomplishment is the same lie, reversed."""
        steps = [step("catia_pad"), step("catia_hole_pattern", ok=False)]
        message = _blank_turn_message(steps, LABELS)
        assert "CATIA: pad" in message
        assert "CATIA: hole pattern" not in message

    def test_repeats_are_listed_once(self) -> None:
        """H2 called sketch_circle twice and hole_pattern twice. A list that
        repeats itself reads as a stutter and buries the shorter list."""
        steps = [step("catia_pad"), step("catia_pad"), step("catia_measure")]
        assert _blank_turn_message(steps, LABELS).count("- CATIA: pad") == 1

    def test_an_unlabelled_tool_still_reads_as_english(self) -> None:
        """`labels` covers the tools the UI knows; a new one must not surface
        as a bare identifier in a message the user reads."""
        message = _blank_turn_message([step("catia_thing_we_added")], {})
        assert "catia thing we added" in message
        assert "catia_thing_we_added" not in message


class TestTheCorrectionNudge:
    """Which correction the model is sent when it goes blank.

    `AGENT_EMPTY_TURN` offers the model the choice of calling another tool.
    That is right when nothing has happened; after twelve successful calls it
    re-opens the loop the model is already stuck in, and the user still gets
    nothing. H2 went blank twice with that nudge in between.
    """

    def test_a_blank_turn_with_no_work_gets_the_original(self) -> None:
        from app.ai import prompts

        assert "If you need a tool, call it" in prompts.AGENT_EMPTY_TURN

    def test_a_blank_turn_after_work_is_told_the_work_already_ran(self) -> None:
        from app.ai import prompts

        nudge = prompts.AGENT_EMPTY_TURN_AFTER_WORK
        assert "ran and succeeded" in nudge
        assert "Do not go silent again" in nudge
        # And it has to say what to write, or "answer" is as vague as silence.
        assert "the numbers you measured" in nudge

    def test_it_does_not_order_a_half_finished_build_to_stop(self) -> None:
        """The first version said "Do not call another tool", and the third H2
        run obeyed it after two sketch circles -- reporting an unfinished
        flange because we told it to, not because the work was done. The code
        knows only that *something* ran, so the instruction has to be about the
        silence, and the model picks which of the two applies."""
        from app.ai import prompts

        nudge = prompts.AGENT_EMPTY_TURN_AFTER_WORK
        assert "Do not call another tool" not in nudge
        assert "If the job is not finished, call the next tool now" in nudge

    def test_it_forbids_claiming_work_the_results_do_not_show(self) -> None:
        """A model told to "write the answer now" can invent the rest of the
        build to have something to write."""
        from app.ai import prompts

        assert "Never describe as done anything the results above do not show" in (
            prompts.AGENT_EMPTY_TURN_AFTER_WORK
        )

    def test_the_agent_picks_between_them_on_whether_anything_ran(self) -> None:
        """Reads the source of , which is where the loop lives.

        Reaching this branch for real needs a provider that returns two empty
        turns in a row and a live conversation to append to -- an integration
        test of the loop, not of the choice being made here."""
        import inspect

        from app.ai import agent

        source = inspect.getsource(agent.stream_agent)
        assert "AGENT_EMPTY_TURN_AFTER_WORK" in source
        assert "if any(s.ok for s in steps)" in source
