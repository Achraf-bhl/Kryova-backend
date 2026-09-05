"""Choosing which tools to *show* the model — master plan 16.1.

The wall this exists for was measured on this machine on 2026-09-05 rather than
read in a paper. With 108 CATIA operations offered, `qwen3-coder:30b` asked for a
mounting flange opened with `catia_pad` on a conversation holding no document,
invented the argument `profile`, invented two tools that have never existed, and
never reached `catia_sketch_create` — on the open kernel *and* on a real CATIA
seat, identically. The schemas alone are ~16k prompt tokens re-evaluated every
turn (`prompt_eval_cached_count: 0`). The published numbers say the same thing:
accuracy falls from ~87% at 500 tools to ~65% at 2,000, and retrieval errors
account for about half of agent failures at scale.

**This narrows what the model is shown. It never narrows what it can call.**
That distinction is the whole design. `ToolBox.call` keeps every tool, so a model
that names something it was not offered still gets it — the call works, and the
turn is not spent on a refusal that would have been a lie. Retrieval is therefore
a pure attention-and-tokens optimisation with no capability cost, and there is no
failure mode where a needed tool has become unreachable. Anything less than that
would be trading correctness for latency, which this codebase does not do.

**Lexical, not embeddings** — the same decision, for the same reasons, as
`app/retrieval/`. What discriminates between tool names here is exact terms:
`fillet`, `pocket`, `dépouille`, `helix`, `M6`. Those are precisely what an
embedding blurs, and half the vocabulary a French user types is already handled
by the bilingual tokenizer this reuses. There is no index to build and nothing to
keep in step with the registry, because the registry *is* the corpus and it is
already in memory.

**Three things are always shown, whatever the message says.**

*The core.* A part cannot be built without `catia_new_part`, a sketch, a profile
and a pad, so those are never subject to a query matching them. The measured
failure was the model skipping exactly these; hiding one because the user said
"flange" rather than "sketch" would be the same bug from the other end.

*What the conversation just used.* Continuity beats similarity: a model that
called `catia_pattern_circular` last turn is probably about to call it again, and
a query that has moved on to "now measure it" would otherwise drop it.

*Everything, when the limit is not smaller than the registry.* Retrieval that
cannot reduce anything must be a no-op rather than a reordering, so a small
deployment behaves exactly as it did before.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from typing import Any, Final, Protocol

#: The modelling loop, the measurement, and the document. Never withheld.
#:
#: Deliberately not "every read-only tool" or "every tool in Part Design" — a
#: rule that broad re-offers most of the registry and gives back the problem.
#: This is the shortest list with which a part can be built and checked at all.
CORE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        # the document a conversation owns
        "catia_new_part",
        "catia_open_document",
        "catia_status",
        # the four-step modelling loop the system prompt teaches
        "catia_sketch_create",
        "catia_sketch_rectangle",
        "catia_sketch_circle",
        "catia_pad",
        "catia_pocket",
        # look at your own work — the prompt requires this after every mutation
        "catia_measure",
        "catia_capture_view",
        "catia_list_features",
        "catia_update",
        # Verification is never withheld. Narrowing the offer must not be able to
        # produce a part nobody checked — that is the failure 16.1 would otherwise
        # cause while fixing a different one.
        "check_part",
    }
)

#: How many tools to show when retrieval is on. Chosen as roughly a third of the
#: current registry: enough that a plausible request keeps its whole vocabulary,
#: small enough that the schemas stop dominating the context. It is a starting
#: point to be measured, not a tuned constant, and the board says so.
DEFAULT_LIMIT: Final = 40


class _Named(Protocol):
    name: str
    description: str


def _terms(text: str) -> set[str]:
    """Tokenised, folded, stemmed — the same treatment the manuals get.

    Reuses `app.retrieval.analyze` rather than splitting on whitespace so that
    accents fold (`dépouille` from `depouille`), technical terms survive stemming
    (`tet4`, `M6`), and a French request reaches an English tool name.
    """
    from app.retrieval.analyze import analyze

    return set(analyze(text))


def _spec_terms(spec: _Named) -> set[str]:
    """The words that identify one tool.

    The name is split on underscores so `catia_pattern_circular` contributes
    `pattern` and `circular` — without that, a name only ever matches a user who
    already knew it, which is the opposite of what retrieval is for.
    """
    name_words = str(spec.name).replace("_", " ")
    return _terms(f"{name_words} {getattr(spec, 'description', '')}")


def score_tool(spec: _Named, query_terms: Collection[str]) -> float:
    """How well one tool matches the request.

    Overlap normalised by the query rather than by the tool, so a long
    description cannot outrank a precise short one simply by containing more
    words. A tool matching two of three asked-for terms beats one matching two of
    twenty.
    """
    if not query_terms:
        return 0.0
    shared = _spec_terms(spec) & set(query_terms)
    if not shared:
        return 0.0
    return len(shared) / len(set(query_terms))


def select_tool_names(
    specs: Sequence[_Named],
    message: str,
    *,
    recent: Iterable[str] = (),
    limit: int = DEFAULT_LIMIT,
) -> set[str]:
    """Which tool names to show this turn.

    Returns names rather than specs so a caller can intersect it with whatever it
    holds — the toolbox keys on names, and handing back objects would make the
    caller match them up again.

    `limit` is a floor on nothing: the core and the recent set are added after
    the scored ones are cut, so the result can exceed `limit` slightly. That is
    the right way round. A limit that could evict `catia_new_part` would
    reintroduce the failure this module was written for.
    """
    everything = {str(spec.name) for spec in specs}
    if limit <= 0 or limit >= len(everything):
        # Not a reordering — genuinely everything, so a deployment small enough
        # not to need retrieval behaves exactly as it did before it existed.
        return everything

    query_terms = _terms(message)
    scored = sorted(
        ((score_tool(spec, query_terms), str(spec.name)) for spec in specs),
        key=lambda pair: (-pair[0], pair[1]),
    )
    chosen = {name for score, name in scored[:limit] if score > 0.0}

    chosen |= CORE_TOOLS & everything
    chosen |= {name for name in recent if name in everything}
    return chosen


def describe_selection(
    specs: Sequence[_Named], chosen: Collection[str]
) -> dict[str, Any]:
    """What retrieval did, for a log line or a board measurement.

    Kept because 16.1's whole justification is a number — schemas as a share of
    the prompt — and a change that cannot be measured cannot be defended.
    """
    everything = {str(spec.name) for spec in specs}
    return {
        "offered": len(chosen),
        "available": len(everything),
        "withheld": sorted(everything - set(chosen))[:20],
        "core_present": sorted(CORE_TOOLS & set(chosen)),
    }


__all__ = [
    "CORE_TOOLS",
    "DEFAULT_LIMIT",
    "describe_selection",
    "score_tool",
    "select_tool_names",
]
