"""Structured output shapes for the AI layer.

Every model here is turned into a JSON Schema and handed to whichever provider
is configured as a generation constraint -- Anthropic's `output_config.format`,
OpenAI's `response_format`, Ollama's `format` -- so decoding is constrained to
match rather than us parsing prose and hoping. `providers/_json_schema.py`
closes the schema first, because the strict providers reject an object that
allows extra properties.

Field descriptions are part of the prompt -- the model reads them, so they are
written for the model, not for a docs page.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.solve.types import LoadCase

Verdict = Literal["safe", "marginal", "yields"]
Confidence = Literal["high", "medium", "low"]


class Finding(BaseModel):
    """One specific, actionable observation about the result."""

    title: str = Field(description="Six words or fewer, e.g. 'Stress concentrates at the fillet'.")
    detail: str = Field(
        description=(
            "Two or three sentences of engineering reasoning. Reference only numbers "
            "given in the input -- never compute or estimate a new one."
        )
    )
    severity: Literal["critical", "warning", "info"]


class DesignSuggestion(BaseModel):
    """A change the engineer could make, and what it would trade away."""

    change: str = Field(
        description="The concrete change, e.g. 'Increase the web thickness to 6 mm'."
    )
    rationale: str = Field(description="Why this addresses the finding.")
    tradeoff: str = Field(
        description="What it costs -- added mass, machining time, material cost. Never omit this."
    )


class ResultInterpretation(BaseModel):
    """A structural engineer's read of one completed linear-static run."""

    verdict: Verdict = Field(
        description=(
            "Restate the solver's own conclusion. 'yields' iff factor_of_safety < 1, "
            "'marginal' iff 1 <= factor_of_safety < 1.5, otherwise 'safe'. "
            "Do not apply your own judgement to this field."
        )
    )
    headline: str = Field(
        description="One sentence a mechanical engineer would accept as the summary."
    )
    findings: list[Finding] = Field(min_length=1, max_length=5)
    suggestions: list[DesignSuggestion] = Field(
        max_length=4,
        description="Empty list is correct when the part passes comfortably and needs no change.",
    )
    confidence: Confidence = Field(
        description=(
            "'low' whenever the mesh is coarse, the run carries warnings, or the load case "
            "looks under-specified. Say so in a finding when you report low confidence."
        )
    )
    caveat: str = Field(
        description=(
            "The single most important limitation of this analysis for this specific part. "
            "Linear static assumes small deflection, static loading and no contact."
        )
    )


class Discrepancy(BaseModel):
    """One thing in the picture that does not agree with what was asked for."""

    what: str = Field(
        description=(
            "The disagreement, in one sentence: what the request asked for and what "
            "the drawing shows instead."
        )
    )
    view: str = Field(
        description=(
            "Which of the named views you saw it in. Use the names given in the "
            "message; if it shows in several, name the clearest one."
        )
    )
    severity: Literal["gross", "minor"] = Field(
        description=(
            "'gross' for something anyone would see at a glance -- a missing feature, "
            "a wrong overall shape, a part built the wrong way round. 'minor' for a "
            "proportion that looks off. If you are weighing which, it is minor."
        )
    )


class VisualCheck(BaseModel):
    """A vision model's read of one or more renders of a part.

    Field order is deliberate and is the one prompt-level thing that reliably
    improves this answer: `describes` is first, so a decoder constrained to this
    schema must state what it can see *before* it reaches the verdict field.
    Asked the other way round, a vision model produces the judgement first and
    then narrates in support of it. It also leaves a reviewer something to check
    the verdict against -- a description of the wrong part is obvious to a human
    in a way that a bare "matches" never is.
    """

    describes: str = Field(
        description=(
            "What you actually see, in two or three sentences, before judging anything. "
            "Overall shape, then the features you can make out and roughly where. "
            "Describe the drawing in front of you, not the part you were told to expect."
        )
    )
    verdict: Literal["matches", "differs", "unsure"] = Field(
        description=(
            "'differs' only when you can name a specific disagreement and say which view "
            "shows it. 'unsure' when these views do not show what the request is about, "
            "or the drawing is too coarse to tell -- that is a useful answer, not a "
            "failure, and it is much better than a guess either way."
        )
    )
    discrepancies: list[Discrepancy] = Field(
        max_length=6,
        description="Empty unless the verdict is 'differs'. One entry per distinct problem.",
    )
    confidence: Confidence = Field(
        description=(
            "How sure you are of the verdict. 'low' whenever the feature in question is "
            "small in the drawing, hidden behind material, or only visible in one view."
        )
    )


class LoadCaseDraft(BaseModel):
    """A load case parsed out of a natural-language description."""

    load_case: LoadCase
    assumptions: list[str] = Field(
        description=(
            "Every value you chose that the user did not state -- material, direction "
            "convention, which face was fixed. One short sentence each."
        )
    )
    unresolved: list[str] = Field(
        description=(
            "Anything genuinely ambiguous that the engineer must confirm before trusting "
            "the run. Empty when the description was complete."
        )
    )
