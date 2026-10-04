"""When the round budget runs out, the last message has to be a report.

Measured on ladder prompt H3, 2026-09-06. The turn used all twenty rounds with
the block built, the fillets on and the pocket profile drawn but not cut. What
the user was left with was:

    "I closed Sketch.2 since we only needed Sketch.1 for the main block shape.
     Now let's create a second sketch for the mounting plate outline on the YZ
     plane:"

followed by the interface's own line, "The agent ran out of tool rounds for
that turn." Three things wrong with that ending, and only the third is the
model's doing:

* It announces work it is about to do and cannot -- the turn is over.
* It names a "mounting plate" nobody asked for, so the user cannot tell what
  is in the part without opening CATIA.
* It never says what *was* built, which is the one thing the user needs to ask
  the right next question.

`AGENT_OUT_OF_STEPS` is the system prompt sent for that closing message, with
tools withdrawn, and it used to say only "Answer with what you have, and say
plainly what is still unresolved and what you would do next". "What you would
do next" is what produced "Now let's create...". The rewrite asks for three
named parts in order -- built, not done, what to ask for next -- and forbids
the future tense.

These are prompt tests: they check the rule is *stated*, since no local model
can be asserted on in CI. Whether qwen3.5:9b then writes a usable report is a
seat measurement, recorded in `docs/GUI_PROMPT_LADDER.md`.
"""

from __future__ import annotations

import inspect

from app.ai import agent, prompts

TEXT = prompts.AGENT_OUT_OF_STEPS.lower()


class TestItAsksForAReport:
    def test_it_asks_what_was_built(self) -> None:
        assert "built" in TEXT

    def test_it_asks_what_was_not_done(self) -> None:
        assert "not" in TEXT and "done" in TEXT

    def test_it_asks_for_the_next_request_the_user_should_make(self) -> None:
        """Not "what I would do next" -- what the *user* should ask for. The
        difference is whether the sentence is actionable by the person reading
        it, who is the only one who can act at that point."""
        assert "ask for next" in TEXT or "ask for" in TEXT

    def test_it_says_the_turn_is_over(self) -> None:
        """A model told only that it "has run out" wrote "Now let's..." next.
        It has to be told it cannot call another tool, in those words."""
        assert "cannot call another" in TEXT


class TestItForbidsTheFailureThatWasMeasured:
    def test_the_future_tense_openers_are_named(self) -> None:
        """Naming the exact phrases beats "do not describe future work": a
        small model follows a banned string and interprets an abstraction."""
        assert "now let's" in TEXT
        assert "next i will" in TEXT

    def test_nothing_may_be_reported_that_the_results_do_not_show(self) -> None:
        """The same rule the rest of the prompt applies to every claim -- a
        turn ending under pressure is where it is most likely to be broken."""
        assert "the results do not show" in TEXT

    def test_it_asks_for_the_numbers_that_were_measured(self) -> None:
        """"A block and some fillets" is not a report. The feature names and
        volumes are in the tool results directly above it."""
        assert "measured" in TEXT and "feature names" in TEXT


class TestItIsTheMessageActuallySent:
    def test_the_exhaustion_path_uses_it(self) -> None:
        """A rule in an unused constant is a rule nobody follows. This is the
        one place the agent loop appends it to the system prompt, with `tools`
        emptied so the model cannot answer with another call."""
        source = inspect.getsource(agent)
        assert 'prompts.AGENT_OUT_OF_STEPS if stop_reason == "step_budget"' in source
        block = source.split("system + closing_instruction", 1)[1][:400]
        assert "tools=[]" in block, (
            "the closing call must withdraw the tools, or the model answers the "
            "budget-exhausted turn with yet another tool call"
        )

    def test_the_hard_fallback_still_exists(self) -> None:
        """The closing call is a live LLM request and can fail. When it does
        the user still gets a sentence rather than an empty bubble."""
        source = inspect.getsource(agent)
        assert "I used all my tool calls for this turn" in source

    def test_every_ending_has_its_own_fallback(self) -> None:
        """Each stop reason the loop can produce gets a sentence about *its* cause;
        a missing one would fall back to "try narrowing the question"."""
        import re

        reasons = set(re.findall(r'stop_reason = "(\w+)"', inspect.getsource(agent)))
        assert reasons == set(agent._CLOSING_FALLBACK)


class TestAnEarlyEndingIsNotCalledRunningOut:
    """`AGENT_ENDED_EARLY`: the gate, escalation and repeated-call exits stop with
    rounds unspent. Telling the model it had used every tool call was false each
    time, and its advice ("ask for less") pointed at the one thing that was fine."""

    EARLY = prompts.AGENT_ENDED_EARLY.lower()

    def test_it_does_not_claim_the_budget_ran_out(self) -> None:
        assert "used every tool call" not in self.EARLY
        assert "not run out of tool calls" in self.EARLY

    def test_it_keeps_the_report_rules(self) -> None:
        assert "cannot call another" in self.EARLY
        assert "now let's" in self.EARLY and "next i will" in self.EARLY
        assert "the results do not show" in self.EARLY

    def test_it_leaves_the_options_to_the_system(self) -> None:
        """The intervention carries the real options; a model inventing its own
        would put two different forks on one screen."""
        assert "do not invent options" in self.EARLY

    def test_it_is_frozen_prose_and_not_built_at_runtime(self) -> None:
        """Same property the four system prompts have: what the model was told
        is readable here, not assembled from settings somewhere."""
        assert isinstance(prompts.AGENT_OUT_OF_STEPS, str)
        assert "{" not in prompts.AGENT_OUT_OF_STEPS
