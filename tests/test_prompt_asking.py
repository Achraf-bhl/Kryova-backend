"""The agent must ask for the sizes rather than invent a whole part.

Measured on the seat, 2026-09-06, ladder prompt E6 -- "Make me a mounting
bracket with two holes", a request that is underspecified on purpose. The agent
built a 60x40x20 bracket with two 10 mm holes and R2 fillets and presented it as
done. Every one of those numbers was invented.

It is worth being precise about why that is a failure, because the geometry was
fine and the tools all did their jobs. A part built entirely from numbers the
agent chose is not the user's part; it is a different design that happens to
answer to the same noun. And it is *more* expensive to recover from than a
question would have been, because the user now has to work out which dimensions
were theirs and which were ours before they can correct any of them.

The rule is not "ask more". `_CORE_BEHAVIOUR` already tells the agent to ask at
most one question and only when the answer is load-bearing, and that is the
right default -- an earlier version of this product interrogated people. The
distinction this pins is between a *detail* that was not given (assume it, say
so, continue) and the *principal dimensions* not being given at all (there is
nothing to assume from). Both directions are asserted here, because a prompt
that only forbade guessing would turn every request into an interview.

The second half of the same E6 answer is the other rule pinned here: it
described holes it had made 15 mm deep in a 20 mm plate as going "through the
top face". Both cannot be true, and the arguments it had sent said which one
was. A description the tool results do not support is a false statement about a
part somebody is going to manufacture.

These are prompt tests, so they check the rule is *stated*, not that a model
obeys it -- no local model can be asserted on in CI. Whether qwen3.5:9b actually
asks is measured on the seat and recorded in docs/GUI_PROMPT_LADDER.md.
"""

from __future__ import annotations

import pytest

from app.ai import prompts

BUILD_PROMPTS = [prompts.AGENT_SYSTEM_CATIA, prompts.AGENT_SYSTEM_CATIA_DOCS]


@pytest.mark.parametrize("prompt", BUILD_PROMPTS, ids=["catia", "catia_docs"])
class TestAskWhenThereIsNothingToAssumeFrom:
    def test_the_rule_is_stated(self, prompt: str) -> None:
        assert "A part needs dimensions before it needs geometry" in prompt

    def test_it_says_what_to_ask_for_and_that_it_is_one_question(self, prompt: str) -> None:
        """An unbounded "ask for what you need" is how the interrogation
        failure comes back. The rule has to bound the question itself."""
        assert "in ONE short question" in prompt
        assert "the overall envelope" in prompt

    def test_it_forbids_the_plausible_starting_point(self, prompt: str) -> None:
        """The specific escape hatch a model reaches for: build something, then
        offer to change it. That is the E6 failure with a caveat attached."""
        assert 'build a "starting point" first and offer to change it' in prompt

    def test_it_does_not_turn_every_request_into_an_interview(self, prompt: str) -> None:
        """The other direction, and the one that costs a user their turn.

        Without this, "60x40x20 plate, two 10 mm holes" invites a question about
        the fillet radius, and the product becomes slower than CATIA.
        """
        assert "Judge that by what is missing, not by how long the request is" in prompt
        assert "omits a fillet radius is buildable" in prompt
        # And the general default it sits under is still there, unweakened.
        assert "Ask ONE clarifying question only when the answer is load-bearing" in prompt


@pytest.mark.parametrize("prompt", BUILD_PROMPTS, ids=["catia", "catia_docs"])
class TestDescribeWhatWasActuallyBuilt:
    def test_the_rule_is_stated(self, prompt: str) -> None:
        assert "Describe the part you built, not the one you meant to build" in prompt

    def test_the_blind_versus_through_case_is_named(self, prompt: str) -> None:
        """Named rather than left general because it is the one that happened,
        and a rule with the actual case in it survives paraphrase."""
        assert "a hole given a depth is blind to that depth" in prompt


def test_the_five_ways_count_matches_the_rules_above_it() -> None:
    """A small thing that rots silently.

    The workflow block closes by summarising the preceding rules as "the N ways
    a build reports success and delivers something other than what was asked
    for". Adding a rule without moving the number leaves the prompt asserting
    its own miscount, which is exactly the kind of small incoherence that makes
    a small model discount the surrounding text.
    """
    prompt = prompts.AGENT_SYSTEM_CATIA
    tail = prompt[prompt.index("Repeated features are patterned") : prompt.index("None of these are rules")]
    rules = [
        "Repeated features are patterned",
        "Read a dimension as the quantity it names",
        "Select the smallest group that matches",
        "When a value you were given will not build",
        "A part needs dimensions before it needs geometry",
    ]
    assert all(rule in tail for rule in rules)
    assert f"the {['one','two','three','four','five','six'][len(rules) - 1]} ways" in prompt


def test_none_of_this_reaches_the_prompts_with_no_build_tools() -> None:
    """AGENT_SYSTEM has no geometry tools at all, so a rule about refusing to
    build would be describing a capability the model was not given -- the same
    mistake `_DOCUMENTATION_LOOKUP` is kept conditional to avoid."""
    for prompt in (prompts.AGENT_SYSTEM, prompts.AGENT_SYSTEM_DOCS):
        assert "A part needs dimensions before it needs geometry" not in prompt
        assert "Describe the part you built" not in prompt
