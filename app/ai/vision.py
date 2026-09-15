"""The visual check: render the part, ask a model whether it matches the request.

Phase 4.2. The renderer (`app/render/`) answers "what does this look like"; this
answers "does that look like what was asked for" — the one question the
measurement layer cannot ask, because a part can satisfy every assertion in a
suite and still be built the wrong way round.

**A filter, never a sign-off.** The master plan states the limitation and this
module is written around it rather than in spite of it: a vision model will
confidently approve a subtly wrong part. What it reliably catches are the gross
errors — a missing feature, a feature on the wrong face, a mirrored part, a
pocket that went through — which are also the common ones. So there is no
`approved` flag here and no boolean a caller could gate a release on. There are
three outcomes and the useful one to act on is `objected`.

**A check that could not run is `unchecked`, never a pass.** The same rule
`assertions.py` applies to an unmeasured assertion and `provenance.py` applies
to an unavailable number. Every way this can fail — no vision model installed,
the provider unreachable, the model unsure, nothing drawn to look at — lands in
`unchecked` with the reason in words. Nothing in the check raises: like
`KnowledgeService.search`, this improves an answer and must never be the reason
there is not one.

**The second half reads a picture a user attached** (P4.2, `AttachmentLook`), and
it does raise, on purpose. It is the `Look` that `app.documents.images` injects,
and that contract turns "no model here can see" into `UnsupportedDocument` and
"the model did not answer" into `ExtractionFailed`, so the attachment row records
*not read, and why*. A description is not a check, and none of it is a pass.

    review = review_shape("a 60x40 plate with a 14 mm hole in the middle", shape)
    if review.objected:
        for one in review.check.discrepancies:
            ...

The verdict is bound to the exact images it was formed from (`views`,
`digests`), because Decision 3 of the master plan says a result is bound to what
produced it — and a stored "looks right" that nobody can tie to a picture is
worth nothing a week later.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from app.ai import prompts
from app.ai.provider import Completion, LLMError, LLMProvider, TokenUsage, VisionUnsupported
from app.ai.sanitise import sanitise_untrusted
from app.ai.schemas import ImageReading, VisualCheck
from app.documents.errors import ExtractionFailed, UnsupportedDocument
from app.documents.images import Sight
from app.render import Render, render_views

logger = logging.getLogger(__name__)

#: Which views a check runs on by default. Three, not the eight the renderer can
#: produce: images dominate the cost of this call, and three mutually
#: perpendicular directions already determine the silhouette from every side.
#: Rendering all eight quadruples the spend to catch the same gross errors, and
#: a caller who knows the feature in question faces backwards can name the views
#: it wants.
DEFAULT_VIEWS: Final[tuple[str, ...]] = ("front", "top", "iso")

#: Ceiling on the request text handed to the model. Long enough for a real brief,
#: short enough that a pasted specification cannot crowd the images out of the
#: context window — which on a local model happens silently.
REQUEST_MAX_CHARS: Final = 4_000

#: Output ceiling. A description, a verdict and a few discrepancies; a model
#: writing more than this is not answering the question.
MAX_TOKENS: Final = 1_200

#: Effort hint. Low on purpose — this is a reading task, not a deliberation, and
#: the reasoning budget on the providers that have one buys nothing here.
EFFORT: Final = "low"

Outcome = Literal["matches", "differs", "unchecked"]


@dataclass(frozen=True)
class VisualReview:
    """What a vision model made of a part, and what it was looking at.

    There is deliberately no `approved` or `passed` property. A caller wanting
    one would be using this as a sign-off, which the phase says it is not; the
    property that exists is `objected`, which is the only one it is safe to
    act on. `matches` means a model looked and saw nothing wrong — useful
    corroboration, and not evidence the part is right.
    """

    outcome: Outcome
    #: Why, when `unchecked`. Empty otherwise. Always a sentence a user can act
    #: on: which model was missing, what was not reachable, what could not be seen.
    reason: str = ""
    check: VisualCheck | None = None
    #: The views looked at, in the order they were shown, and the digest of each
    #: image. Together these say exactly what this verdict is about.
    views: tuple[str, ...] = ()
    digests: tuple[str, ...] = ()
    usage: TokenUsage = field(default_factory=TokenUsage)

    @property
    def objected(self) -> bool:
        """The model named something it says is wrong. The one flag worth gating on."""
        return self.outcome == "differs"

    @property
    def checked(self) -> bool:
        """A model actually looked. False for every kind of not-run, including 'unsure'."""
        return self.outcome != "unchecked"

    @property
    def gross(self) -> tuple[str, ...]:
        """The obvious problems only — what a person would see at a glance."""
        if self.check is None:
            return ()
        return tuple(one.what for one in self.check.discrepancies if one.severity == "gross")

    def summary(self) -> str:
        """One line for a log or a step label. Never fabricates a verdict."""
        if self.outcome == "unchecked":
            return f"Not visually checked: {self.reason}"
        looked = ", ".join(self.views) or "no views"
        if self.outcome == "matches":
            return f"Looks like what was asked for ({looked})."
        count = len(self.check.discrepancies) if self.check else 0
        return f"Looks wrong in {count} respect(s) ({looked})."


def _unchecked(reason: str, renders: Sequence[Render] = ()) -> VisualReview:
    """The honest empty answer, still bound to whatever was rendered."""
    return VisualReview(
        outcome="unchecked",
        reason=reason,
        views=tuple(one.view for one in renders),
        digests=tuple(one.digest for one in renders),
    )


def review(
    request: str,
    renders: Sequence[Render],
    *,
    provider: LLMProvider,
) -> VisualReview:
    """Ask a vision model whether these renders show what `request` describes.

    Never raises. Every failure is an `unchecked` review carrying the reason,
    because a build must not fail on account of a model that could not be
    reached — the measured checks are what decide that.
    """
    if not renders:
        return _unchecked("there was nothing to render.")
    if all(one.is_blank for one in renders):
        return _unchecked(
            "every view came back empty, so there is no geometry to look at.", renders
        )

    views = tuple(one.view for one in renders)
    digests = tuple(one.digest for one in renders)
    # The request is the user's own words rather than tool output, so it is not
    # `<tool_result_data>` material — but it is still volatile text going into a
    # prompt, and the system half above tells the model to treat it as data.
    brief = sanitise_untrusted(request, max_chars=REQUEST_MAX_CHARS)

    try:
        answered: Completion[VisualCheck] = provider.look(
            system=prompts.VISUAL_CHECK_SYSTEM,
            user=prompts.visual_check_user_message(brief, views),
            images=[one.png for one in renders],
            schema=VisualCheck,
            effort=EFFORT,
            max_tokens=MAX_TOKENS,
        )
    except VisionUnsupported as exc:
        return _unchecked(str(exc), renders)
    except LLMError as exc:
        logger.info("Visual check did not run: %s", exc)
        return _unchecked(str(exc), renders)

    seen = answered.value
    if seen.verdict == "unsure":
        # Folded into `unchecked` rather than kept as a fourth outcome: a model
        # that cannot tell has not checked the part, and giving that its own
        # value would invite a caller to treat "not differs" as a pass.
        return VisualReview(
            outcome="unchecked",
            reason="the model could not tell from these views: " + seen.describes,
            check=seen,
            views=views,
            digests=digests,
            usage=answered.usage,
        )

    # A 'differs' with nothing to point at is the failure mode the prompt works
    # hardest against, and it still happens. Reported as unchecked rather than
    # as an objection: an unlocatable complaint cannot be acted on and would
    # block a build nobody can then fix.
    if seen.verdict == "differs" and not seen.discrepancies:
        return VisualReview(
            outcome="unchecked",
            reason=(
                "the model said the part differs but named nothing specific, so there "
                "is nothing to act on: " + seen.describes
            ),
            check=seen,
            views=views,
            digests=digests,
            usage=answered.usage,
        )

    return VisualReview(
        outcome=seen.verdict,
        check=seen,
        views=views,
        digests=digests,
        usage=answered.usage,
    )


def review_shape(
    request: str,
    shape: Any,
    *,
    provider: LLMProvider,
    views: tuple[str, ...] = DEFAULT_VIEWS,
) -> VisualReview:
    """Render a shape from several views and review them together.

    One call with several images rather than one call per view, deliberately:
    the views are pictures of one part and a model shown all of them can say
    "the hole is in the top face" where three separate calls each see a hole and
    none can place it. It is also cheaper — one copy of the prompt, not three.

    The views are framed together (`render_views`), so the part is at one scale
    across all of them and a feature does not appear to change size between two
    pictures of the same object.
    """
    try:
        rendered = render_views(shape, views)
    except Exception as exc:  # noqa: BLE001 - rendering must not fail a build
        logger.info("Visual check could not render: %s", exc)
        return _unchecked(f"the part could not be rendered: {exc}")
    return review(request, [rendered[name] for name in views], provider=provider)


# ---------------------------------------------------------------------------
# Reading a picture somebody attached (P4.2).
# ---------------------------------------------------------------------------

#: Output ceiling for describing an attached picture. A few sentences and the
#: text on a drawing's title block fit well inside it; it is also the only bound
#: on the description's length, because `ImageReading` puts none in the schema.
ATTACHMENT_MAX_TOKENS: Final = 1_500


def looks_with(provider: LLMProvider) -> str:
    """The model `provider.look` sends pictures to: the vision model if one is set.

    Read from the provider rather than from settings, because the provider is
    what actually makes the call. Every provider here keeps it as
    `_vision_model` beside `model`, and the seam has no public name for it.
    """
    return str(getattr(provider, "_vision_model", None) or provider.model)


class AttachmentLook:
    """`app.documents.images.Look`, answered by `provider`, and what it spent.

    A class rather than a closure so the caller can read `usage` after the
    attachment is read and post it to the ledger. A picture is a model call, and
    a model call that never reaches the ledger is one the daily allowance and
    the bill both miss.

    **The provider's own refusal is the gate for a model that cannot see.**
    Ollama answers `/api/show` with the model's capabilities and `look` raises
    `VisionUnsupported` before any image is sent, because Ollama would otherwise
    drop the picture and describe nothing. That becomes `UnsupportedDocument`
    here, so the attachment is recorded as not read, with the reason, and never
    as an empty picture.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.model = looks_with(provider)
        self.usage = TokenUsage()
        #: How many pictures the model answered for. A call that answered is
        #: posted to the ledger even at zero tokens, as `usage.record` asks.
        self.answered = 0

    def __call__(self, image: bytes, image_format: str) -> Sight:
        try:
            answered: Completion[ImageReading] = self.provider.look(
                system=prompts.ATTACHED_IMAGE_SYSTEM,
                user=prompts.attached_image_user_message(image_format),
                images=[image],
                schema=ImageReading,
                effort=EFFORT,
                max_tokens=ATTACHMENT_MAX_TOKENS,
            )
        except VisionUnsupported as exc:
            raise UnsupportedDocument(
                f"the picture was not read, because the configured model cannot see "
                f"images. {exc}"
            ) from exc
        except LLMError as exc:
            # Unreachable, refused, or answered with something unusable. A fault,
            # not a capability, so it is `FAILED` rather than `UNSUPPORTED`.
            logger.info("An attached picture was not described: %s", exc)
            raise ExtractionFailed(
                f"the vision model did not describe the picture: {exc} Attach it again "
                "once the model is available to have it read.",
                short="the vision model did not answer",
            ) from exc

        self.usage = self.usage + answered.usage
        self.answered += 1
        reading = answered.value
        return Sight(
            describes=reading.describes,
            visible_text=tuple(reading.visible_text),
            seen_by=f"vision:{self.model}",
        )


def attachment_look(provider: LLMProvider) -> AttachmentLook:
    """The `Look` an attachment reads a PNG or JPEG with, bound to `provider`."""
    return AttachmentLook(provider)


__all__ = [
    "ATTACHMENT_MAX_TOKENS",
    "DEFAULT_VIEWS",
    "EFFORT",
    "MAX_TOKENS",
    "REQUEST_MAX_CHARS",
    "AttachmentLook",
    "Outcome",
    "VisualReview",
    "attachment_look",
    "looks_with",
    "review",
    "review_shape",
]
