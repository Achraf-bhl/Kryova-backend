"""Choosing which tools to *show* the model — master plan 16.1.

Not to be confused with `app/retrieval/`, which is the BM25 index over the
CATIA and FEA manuals. That one retrieves *documents*; this one retrieves
*tools*. The names are close and the jobs are unrelated, which is why this
module is `tool_retrieval` and not `retrieval`.

The wall this exists for was measured on this machine rather than read in a
paper. With 108 CATIA operations offered, `qwen3-coder:30b` asked for a mounting
flange opened with `catia_pad` on a conversation holding no document, invented
the argument `profile`, invented two tools that have never existed, and never
reached `catia_sketch_create` — on the open kernel *and* on a real CATIA seat,
identically. The schemas alone are ~16k prompt tokens re-evaluated every turn
(`prompt_eval_cached_count: 0`). The published numbers say the same thing:
accuracy falls from ~87% at 500 tools to ~65% at 2,000, and retrieval errors
account for about half of agent failures at scale.

And on 2026-09-06 the payload size stopped being only a token bill.
`qwen3.5:9b` — a model that fits this machine's 8 GB card entirely, at 76% GPU
residency against `qwen3-coder:30b`'s 28% — was *slower and worse* on the same
rung-3 prompt: it placed four holes at four wrong coordinates, re-padded the
bore sketch turning the hole into a boss, and gave up. The conclusion recorded
in `docs/verification-2026-09-06/REPORT.md` is that general tool-calling at 9B
does not survive a 40-tool payload with interdependent geometric state. **Shrink
the payload and a model that fits the card becomes usable**, which changes the
cost of every gate run. That is what this is for.

**This narrows what the model is shown. It never narrows what it can call.**
That distinction is the whole design. `ToolBox.call` keeps every tool, so a
model that names something it was not offered still gets it — the call works,
and the turn is not spent on a refusal that would have been a lie. Retrieval is
therefore a pure attention-and-tokens optimisation with no capability cost.

**Recall beats precision here, and the two are not symmetric.** Offering a tool
that turns out to be unnecessary costs prompt tokens and a little of the model's
attention. Failing to offer one costs a *wrongly built part*, and it costs it
silently: there is no error, the model simply does something else, and the
transcript reads as a success. Every widening rule below (`INTENT_FAMILIES`,
domain expansion, prefix families) is a recall device, deliberately loose, and
each one is allowed to be loose precisely because its worst case is tokens.

That is the one place this departs from `app/catia_kb/recognise.py`, whose
two-tier `NEVER_BARE` / `AMBIGUOUS_WORDS` design is the most carefully tuned
precision work in the repo. It has to be: a false match there puts a wrong menu
path in front of an engineer. A false match here adds a schema nobody uses.
Same vocabulary, opposite error cost — so this module *consumes* `recognise`
rather than copying its caution.

**Lexical, not embeddings** — the same decision, for the same reasons, as
`app/retrieval/`. What discriminates between tool names here is exact terms:
`fillet`, `pocket`, `dépouille`, `helix`, `M6`. Those are precisely what an
embedding blurs. There is no index to build and nothing to keep in step with the
registry, because the registry *is* the corpus and it is already in memory.

**Four things are always shown, whatever the message says.**

*The core* (`CORE_TOOLS`). A part cannot be built without `catia_new_part`, a
sketch, a profile and a pad, so those are never subject to a query matching
them. The measured failure was the model skipping exactly these; hiding one
because the user said "flange" rather than "sketch" would be the same bug from
the other end.

*Everything the frozen system prompts teach* (`prompt_taught_tools`). This one
is a defect that shipped: on 2026-09-06 the four frozen prompts named 16 tools
the registry has, and the selector withheld five to nine of them on every
realistic message — including `catia_set_parameter`, which the rung-3 prompt is
*entirely about* and which was withheld on all five messages measured. A prompt
that describes a tool the model was not given teaches it to hallucinate a call;
that is the whole reason there are four frozen prompts rather than one built by
concatenation, and 16.1 was quietly undoing it. Scanned from the prompt text
rather than tabulated, so it cannot fall behind an edit to the prompt.

*What the conversation just used.* Continuity beats similarity: a model that
called `catia_pattern_circular` last turn is probably about to call it again,
and a query that has moved on to "now measure it" would otherwise drop it.

*Everything, when the limit is not smaller than the registry.* Retrieval that
cannot reduce anything must be a no-op rather than a reordering, so a small
deployment behaves exactly as it did before.

**The limit is a ceiling, not a quota.** Slots left over are left empty. Filling
them with the alphabetical remainder would contradict the finding this module is
built on — that a bigger payload is worse — so a selection of 25 out of 40 is
the right answer and not an under-performing one.

**Every inclusion carries its reason.** `select()` returns a `Selection` whose
`Choice` rows say which rule put each tool in the offer and on what evidence, so
a wrong offer is diagnosable from a log line rather than by re-deriving the
scoring by hand. 16.1's justification is a number; so is its debugging.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
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

#: A term the user wrote outranks a term the CATIA reference inferred, at equal
#: overlap. Expansion is a recall device and must not be able to reorder the
#: model's own words — the same rule `expand_query` follows for the manuals.
DOMAIN_WEIGHT: Final = 0.5

#: A term carried by more than this share of the registry tells you nothing
#: about which tool to use, so it is dropped from the query before scoring.
#:
#: This is BM25's inverse-document-frequency intuition on a 110-document corpus,
#: and it is *derived from the registry rather than tabulated* — a stop-list
#: written by hand would be a list of the words that happened to be common on
#: the day it was written. Measured 2026-09-06: without it, the rung-3 prompt
#: scored 31 tools above zero, among them `catia_boolean`, `catia_curve_spiral`
#: and `catia_analysis_part`, on shared words like "one", "start" and "part".
#: Those are not matches; they are the corpus talking to itself.
COMMON_TERM_SHARE: Final = 0.20

#: A term in a tool's *name* is a match. A term only in its description is
#: corroboration, and one of those alone is not enough to offer the tool.
#:
#: Names are curated identifiers; descriptions are prose written for a model to
#: read, and they contain ordinary English verbs. Measured 2026-09-06: "make it
#: weigh 2.4 kg" offered `catia_boolean`, `catia_body_create`, `catia_rib` and
#: `catia_join`, every one of them on the word *make* appearing in its
#: description. No frequency threshold catches that — `make` is in fifteen of a
#: hundred and ten descriptions, which is genuinely discriminating by
#: frequency and worthless by meaning.
#:
#: The tools this rule drops are not lost: `catia_set_material` also shares only
#: a description term here (`kg`) and is offered anyway, by the `mass target`
#: intent family. That is the division of labour — lexical matching handles what
#: the request *names*, the intent tables handle what the task *needs*.
DESCRIPTION_WEIGHT: Final = 0.35

#: The request's own words always get at least this many slots, even on a turn
#: where the unconditional floor has already spent the budget. Without it a
#: large floor silently starves the matching, on precisely the turn where what
#: the user actually said matters most.
MIN_MATCH_SLOTS: Final = 8

#: Intent families: what a *task* needs, as opposed to what its nouns name.
#:
#: Every one of these is a transcript from `docs/verification-2026-09-0{5,6}/`,
#: not a guess at what an engineer might say. The rung-3 prompt — "adjust the
#: thickness until the measured mass is within 20 grams of 2.4 kg" — contains no
#: word that lexically resembles `catia_set_parameter`, and the agent
#: consequently rebuilt the pad three times instead of changing it, in four
#: sessions running. Lexical matching cannot fix that, because the tool the task
#: needs shares no vocabulary with the way the task is asked for. A small
#: explicit table can, and is honest about being a table.
#:
#: Triggers are matched against the analysed (folded, lightly stemmed) message,
#: so they are written here as the forms `app.retrieval.analyze` actually
#: produces — `thicknes`, not `thickness`.
INTENT_FAMILIES: Final[dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    # "make it weigh 2.4 kg" — the ladder's rung 3, and the one the local model
    # has never once got to without help.
    "mass target": (
        (
            "weigh", "weight", "mass", "kg", "kilogram", "gram", "grammes",
            "gramme", "heavy", "heavier", "light", "lighter", "density",
        ),
        (
            "catia_set_material",
            "catia_measure",
            "catia_list_parameters",
            "catia_set_parameter",
            "check_part",
        ),
    ),
    # "adjust the thickness until…". Measured 2026-09-06: told to adjust, the
    # agent called `catia_feature_rename` and padded a second time. The tool it
    # needed existed and was not in the offer.
    "correction": (
        (
            "adjust", "change", "instead", "increase", "decrease", "reduce",
            "resize", "tweak", "until", "thicker", "thinner", "bigger",
            "smaller", "target", "iterate", "converge", "tolerance", "within",
            "parameter", "parametric", "dimension", "redo", "rework", "fix",
        ),
        (
            "catia_list_parameters",
            "catia_set_parameter",
            "catia_list_features",
            "catia_measure",
            "catia_update",
            "check_part",
        ),
    ),
    # "check it clears through the travel" — rung 5. Measured with the shipped
    # selector this message matched nothing at all and collapsed the offer to
    # the core: nine tools out of a budget of forty, and not one of them able to
    # measure a clearance.
    "clearance": (
        (
            "clear", "clears", "clearance", "interfere", "interference",
            "clash", "collide", "collision", "gap", "fit", "fits", "travel",
            "stroke", "envelope", "reach", "swept", "rub", "foul", "obstruct",
        ),
        (
            "catia_measure_between",
            "catia_measure_item",
            "catia_list_faces",
            "catia_measure",
            "catia_assembly_clash",
        ),
    ),
    # "four M8 clearance holes on a 70 mm bolt circle" — rung 2, which passes,
    # and which the pattern tools are the whole point of.
    "holes": (
        (
            "hole", "bore", "bored", "drill", "drilled", "bolt", "tap",
            "tapped", "thread", "threaded", "counterbore", "countersink",
            "clearance", "m6", "m8", "m10", "m12", "pcd",
        ),
        (
            "catia_hole",
            "catia_hole_at",
            "catia_pattern_circular",
            "catia_pattern_rectangular",
            "catia_thread",
            "catia_sketch_circle",
            "catia_pocket",
        ),
    ),
    "edges": (
        (
            "fillet", "round", "rounded", "radius", "radii", "chamfer",
            "bevel", "deburr", "blend", "conge", "depouille", "draft",
        ),
        (
            "catia_fillet",
            "catia_fillet_edges",
            "catia_fillet_variable",
            "catia_chamfer",
            "catia_draft",
            "catia_list_edges",
        ),
    ),
    # Verification vocabulary. `check_part` and `catia_measure` are already core;
    # the rest is what "look at it and tell me if it is right" actually needs.
    "inspection": (
        (
            "check", "verify", "verified", "confirm", "measure", "measured",
            "inspect", "wrong", "correct", "look", "see", "show", "picture",
            "screenshot", "view", "report", "prove", "evidence",
        ),
        (
            "catia_measure",
            "catia_measure_item",
            "catia_capture_view",
            "catia_list_features",
            "catia_list_faces",
            "check_part",
        ),
    ),
    # A mass answer is wrong by a factor of three if nobody set the material —
    # and `set_material` silently falls back to steel, so it is worth reaching.
    "material": (
        (
            "steel", "aluminium", "aluminum", "alloy", "titanium", "brass",
            "bronze", "plastic", "abs", "nylon", "material", "cast", "iron",
        ),
        ("catia_set_material", "catia_measure", "catia_list_parameters"),
    ),
}

#: `catia_x_y_z` -> the family prefix `catia_x_y`. Only two-segment prefixes
#: count: `catia_sketch` and `catia_surface` are families, `catia` is not.
_FAMILY_RE: Final = re.compile(r"^(catia_[a-z0-9]+_[a-z0-9]+)_[a-z0-9_]+$")

#: Anything shaped like a tool name, for scanning the frozen prompts.
_TOOL_NAME_RE: Final = re.compile(r"\bcatia_[a-z0-9_]+\b")


class _Named(Protocol):
    name: str
    description: str


# ---------------------------------------------------------------------------
# What the prompts already promised
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def prompt_taught_tools() -> frozenset[str]:
    """Every tool name the frozen system prompts put in front of the model.

    Read out of the prompt text rather than tabulated here, so it cannot fall
    behind an edit to `app/ai/prompts.py`. That is the point: a hand-kept list
    would be correct on the day it was written and wrong the first time somebody
    added a worked example to a prompt, and the symptom would be a model
    confidently calling a tool it was never handed.

    Cheap and cached — it is a regex over the module's own string constants,
    which are already in memory, run once per process.
    """
    from app.ai import prompts

    text = "\n".join(
        value for name, value in vars(prompts).items()
        if not name.startswith("_") and isinstance(value, str)
    )
    found = set(_TOOL_NAME_RE.findall(text))

    # The tools with no `catia_` prefix have no shape to match on, so they come
    # from the one authoritative list of them.
    try:
        from app.ai.tools import BUILTIN_TOOL_LABELS

        found |= {name for name in BUILTIN_TOOL_LABELS if name in text}
    except Exception:  # pragma: no cover - tools imports this module's siblings
        pass
    return frozenset(found)


# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------


def _terms(text: str) -> list[str]:
    """Tokenised, folded, stemmed — the same treatment the manuals get.

    Reuses `app.retrieval.analyze` rather than splitting on whitespace so that
    accents fold (`dépouille` from `depouille`), technical terms survive
    stemming (`tet4`, `M6`), and a French request reaches an English tool name.
    """
    from app.retrieval.analyze import analyze

    return list(analyze(text))


@lru_cache(maxsize=512)
def _spec_terms(name: str, description: str) -> frozenset[str]:
    """The words that identify one tool.

    The name is split on underscores so `catia_pattern_circular` contributes
    `pattern` and `circular` — without that, a name only ever matches a user who
    already knew it, which is the opposite of what retrieval is for.

    Cached on the pair rather than on the spec object: specs are rebuilt per
    toolbox, so caching on identity would never hit.
    """
    return frozenset(_terms(f"{name.replace('_', ' ')} {description}"))


@lru_cache(maxsize=1)
def _family_triggers() -> tuple[tuple[str, frozenset[str], tuple[str, ...]], ...]:
    """`INTENT_FAMILIES` with its triggers analysed, once."""
    return tuple(
        (label, frozenset(term for word in words for term in _terms(word)), tools)
        for label, (words, tools) in INTENT_FAMILIES.items()
    )


def discriminating_terms(specs: Sequence[_Named]) -> frozenset[str]:
    """The registry's own vocabulary, minus the words every tool uses.

    A term in more than `COMMON_TERM_SHARE` of the descriptions cannot separate
    one tool from another, and a term in none of them cannot match anything, so
    both are dropped. What is left is the set of words that are worth scoring —
    computed from the registry in front of us, so it tracks the registry.

    Cheap: `_spec_terms` is memoised, so this is a few thousand set operations
    over data already in memory.
    """
    counts: dict[str, int] = {}
    for spec in specs:
        for term in _spec_terms(str(spec.name), str(getattr(spec, "description", ""))):
            counts[term] = counts.get(term, 0) + 1
    ceiling = max(1, int(len(specs) * COMMON_TERM_SHARE))
    return frozenset(term for term, count in counts.items() if count <= ceiling)


def _domain_terms(message: str) -> dict[str, str]:
    """CATIA vocabulary the message implies, mapped back to what implied it.

    This is the "build on `recognise` rather than beside it" seam. A user asking
    for "a 60 mm diameter bore" writes no word resembling `catia_hole`;
    `recognise` knows that *bore* means Hole, and *round* means Edge Fillet, and
    *dépouille* means Draft Angle, and it knows it in five interface languages.
    Reusing it means the tool selector speaks French for free.

    `assume_catia=True` because by the time a message reaches the agent the
    subject is established — this is a CAD conversation with a CATIA document
    bound to it — which is exactly the condition that flag documents. It lowers
    the bar for ambiguous single words and never raises it.

    Returns term -> the entry name that contributed it, so a `Choice` can say
    *"because 'bore' means Hole"* instead of just naming a score. Never raises:
    `CatiaKnowledge` and `recognise` both contract not to, and a widening device
    must never be the reason a turn fails.
    """
    try:
        from app.catia_kb.recognise import recognise

        found = recognise(message, limit=8, assume_catia=True)
    except Exception:  # pragma: no cover - the reference is optional to us
        return {}

    out: dict[str, str] = {}
    for match in found.matches:
        entry = match.entry
        for surface in (entry.name, *entry.aliases[:2]):
            for term in _terms(surface):
                out.setdefault(term, entry.name)
    return out


def _split_terms(spec: _Named) -> tuple[frozenset[str], frozenset[str]]:
    """One tool's terms, separated into name and description-only."""
    name = str(spec.name)
    in_name = frozenset(_terms(name.replace("_", " ")))
    everything = _spec_terms(name, str(getattr(spec, "description", "")))
    return in_name, everything - in_name


def score_tool(
    spec: _Named,
    query_terms: Collection[str],
    domain_terms: Collection[str] = (),
) -> float:
    """How well one tool matches the request.

    Overlap normalised by the query rather than by the tool, so a long
    description cannot outrank a precise short one simply by containing more
    words. A tool matching two of three asked-for terms beats one matching two
    of twenty.

    A term in the tool's name counts fully; a term only in its description
    counts `DESCRIPTION_WEIGHT`, because names are curated and descriptions are
    prose. `domain_terms` are the CATIA names `recognise` inferred, scored at
    `DOMAIN_WEIGHT` so an inferred term can add a tool to the offer but can
    never reorder the model's own words above it.
    """
    asked = set(query_terms)
    if not asked:
        return 0.0
    in_name, in_text = _split_terms(spec)
    direct = (len(in_name & asked) + DESCRIPTION_WEIGHT * len(in_text & asked)) / len(asked)
    if not domain_terms:
        return direct
    implied = set(domain_terms)
    inferred = (
        len(in_name & implied) + DESCRIPTION_WEIGHT * len(in_text & implied)
    ) / len(implied)
    return direct + DOMAIN_WEIGHT * inferred


# ---------------------------------------------------------------------------
# The selection, and why
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Choice:
    """One tool in the offer, and the rule that put it there."""

    name: str
    #: A stable code, so a log can be counted: core, prompt, recent, match,
    #: domain, intent, family.
    rule: str
    #: The evidence, in words. What a human reads when the offer looks wrong.
    detail: str
    score: float = 0.0

    def __str__(self) -> str:
        return f"{self.name} ({self.rule}: {self.detail})"


@dataclass(frozen=True, slots=True)
class Selection:
    """What was offered this turn, and why each of it was.

    Explainability is not decoration here. The failure this module can cause is
    silent — a needed tool is absent, the model does something else, and the
    part is wrong with every call returning `ok`. The only way that gets
    diagnosed is by reading back what the offer was and what rule shaped it, so
    the reasons are part of the return value rather than a debug flag.
    """

    choices: tuple[Choice, ...] = ()
    available: int = 0
    limit: int = 0
    #: Empty when the selector was a no-op (limit unset, or not smaller than the
    #: registry). A caller can then skip narrowing entirely.
    narrowed: bool = False
    _index: dict[str, Choice] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._index.update({choice.name: choice for choice in self.choices})

    def names(self) -> set[str]:
        return set(self._index)

    def why(self, name: str) -> str | None:
        """Why `name` was offered, in words, or None if it was not."""
        choice = self._index.get(name)
        return None if choice is None else f"{choice.rule}: {choice.detail}"

    def by_rule(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for choice in self.choices:
            out.setdefault(choice.rule, []).append(choice.name)
        return {rule: sorted(names) for rule, names in sorted(out.items())}

    def to_dict(self) -> dict[str, Any]:
        """A log line. 16.1's justification is a number, so is its debugging."""
        return {
            "offered": len(self.choices),
            "available": self.available,
            "limit": self.limit,
            "narrowed": self.narrowed,
            "by_rule": self.by_rule(),
        }


def select(
    specs: Sequence[_Named],
    message: str,
    *,
    recent: Iterable[str] = (),
    context: str = "",
    limit: int = DEFAULT_LIMIT,
) -> Selection:
    """Choose the tools to show, with a reason for each.

    `context` is the conversation so far — earlier user turns, not the whole
    transcript. It is scored, at the same weight as the message: an engineer who
    said "I need four M8 holes" two turns ago and now says "go on" must not lose
    the hole tools because this turn's words were thin. It is deliberately *user*
    text only; scoring the assistant's own replies would let the model widen its
    own offer, which is a loop with no floor.

    The floor — core, prompt-taught, recent — is unconditional and may exceed
    `limit`. That is the right way round: a limit that could evict
    `catia_new_part` would reintroduce the failure this module was written for,
    and a limit that could evict a tool the system prompt names would teach the
    model to hallucinate the call.
    """
    everything = {str(spec.name) for spec in specs}
    if limit <= 0 or limit >= len(everything):
        # Not a reordering — genuinely everything, so a deployment small enough
        # not to need retrieval behaves exactly as it did before it existed.
        return Selection(
            choices=tuple(
                Choice(name, "all", "retrieval is a no-op at this registry size")
                for name in sorted(everything)
            ),
            available=len(everything),
            limit=limit,
            narrowed=False,
        )

    by_name: dict[str, _Named] = {str(spec.name): spec for spec in specs}
    chosen: dict[str, Choice] = {}

    def take(name: str, rule: str, detail: str, score: float = 0.0) -> bool:
        if name not in everything or name in chosen:
            return False
        chosen[name] = Choice(name, rule, detail, score)
        return True

    # -- the floor, in order of how load-bearing it is ----------------------
    for name in sorted(CORE_TOOLS):
        take(name, "core", "the modelling loop and the measurement, never withheld")
    for name in sorted(prompt_taught_tools()):
        take(name, "prompt", "named in the frozen system prompt, so it must exist")
    for name in recent:
        take(name, "recent", "this conversation already used it")

    # -- what the request actually says ------------------------------------
    # Two term sets, deliberately. `spoken` is everything the user wrote and is
    # what the intent triggers below are matched against, because those triggers
    # are named explicitly and do not need to be discriminating. `query_terms`
    # is `spoken` with the registry's own filler words removed, and is what gets
    # scored — a shared "one" or "part" is the corpus talking to itself.
    spoken = set(_terms(f"{context} {message}" if context else message))
    useful = discriminating_terms(specs)
    # A bare number never names a tool, and an engineering request is mostly
    # numbers. Measured: "make it weigh 2.4 kg" analysed to `2` and `4`, which
    # matched `catia_boolean`, `catia_body_create` and `catia_point_on_surface`
    # through phrases like "2 points" in their descriptions. `M8` and `tet4`
    # survive — they are not bare.
    query_terms = {term for term in spoken & useful if not term.isdigit()}
    domain = {term: name for term, name in _domain_terms(message).items() if term in useful}
    scored = sorted(
        ((score_tool(spec, query_terms, domain), str(spec.name)) for spec in specs),
        key=lambda pair: (-pair[0], pair[1]),
    )
    # The budget the request's own words get. Whatever the floor has not spent,
    # but never less than `MIN_MATCH_SLOTS`: a floor that filled the limit must
    # not be able to silence the message that was actually sent.
    budget = max(limit - len(chosen), MIN_MATCH_SLOTS)
    matched = 0
    for score, name in scored:
        if score <= 0.0 or matched >= budget:
            break
        in_name, in_text = _split_terms(by_name[name])
        # A name term is a match on its own; a lone description term is not.
        # See DESCRIPTION_WEIGHT for the measurement behind that asymmetry.
        named = sorted((in_name & query_terms) | (in_name & set(domain)))
        described_hits = sorted((in_text & query_terms) | (in_text & set(domain)))
        if not named and len(described_hits) < 2:
            continue
        matched += 1
        shared = sorted(set(named + described_hits) & query_terms)
        if shared:
            take(name, "match", "shares " + ", ".join(shared[:4]), score)
        else:
            inferred = sorted({domain[term] for term in set(named + described_hits) & set(domain)})
            take(name, "domain", "the request implies " + ", ".join(inferred[:3]), score)

    # -- what the *task* needs, whatever nouns it used ----------------------
    # Unconditional, like the floor. These tables are small, every entry is a
    # transcript, and the failure they answer is a tool the task needs sharing
    # no vocabulary with the way the task was asked for. Capping them would put
    # that failure back for exactly the long, detailed requests that hit the cap.
    for label, triggers, tools in _family_triggers():
        hit = triggers & spoken
        if not hit:
            continue
        for name in tools:
            take(name, "intent", f"{label} ({', '.join(sorted(hit)[:3])})")

    # -- siblings of what matched, while there is room ---------------------
    # A model that wants a loft usually wants a fill or a sew next, and the
    # vocabulary is prefix-clustered, so the family is a cheap recall device.
    # This is the speculative rule, so it is the one the limit binds: it fills
    # leftover slots and never creates them.
    families: list[str] = []
    for name in list(chosen):
        found = _FAMILY_RE.match(name)
        if found and found.group(1) not in families:
            families.append(found.group(1))
    for prefix in families:
        for name in sorted(everything):
            if len(chosen) >= limit:
                break
            if name.startswith(prefix + "_"):
                take(name, "family", f"sibling of {prefix}_*")

    return Selection(
        choices=tuple(chosen.values()),
        available=len(everything),
        limit=limit,
        narrowed=True,
    )


def select_tool_names(
    specs: Sequence[_Named],
    message: str,
    *,
    recent: Iterable[str] = (),
    context: str = "",
    limit: int = DEFAULT_LIMIT,
) -> set[str]:
    """Which tool names to show this turn.

    Names rather than specs so a caller can intersect with whatever it holds —
    the toolbox keys on names, and handing back objects would make the caller
    match them up again. `select()` is the same thing with the reasons attached;
    this is the shorthand for a caller that only needs the set.
    """
    return select(specs, message, recent=recent, context=context, limit=limit).names()


def describe_selection(specs: Sequence[_Named], chosen: Collection[str]) -> dict[str, Any]:
    """What retrieval did, for a log line or a board measurement.

    Kept because 16.1's whole justification is a number — schemas as a share of
    the prompt — and a change that cannot be measured cannot be defended. Takes
    a bare name collection so it also works on a selection that came from
    somewhere else; `Selection.to_dict` is the richer answer when you have one.
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
    "COMMON_TERM_SHARE",
    "DESCRIPTION_WEIGHT",
    "DOMAIN_WEIGHT",
    "MIN_MATCH_SLOTS",
    "INTENT_FAMILIES",
    "Choice",
    "Selection",
    "describe_selection",
    "discriminating_terms",
    "prompt_taught_tools",
    "score_tool",
    "select",
    "select_tool_names",
]
