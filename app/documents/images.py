"""Pictures a user attached, described by a model that can see (P4.2).

A photograph of a failed weld, a phone shot of a drawing, a screenshot of a
supplier's table: none of them has a text layer, and the only thing here that
can say what one shows is a vision model. So this module hands the picture to
one and turns what comes back into fragments, the way every other reader does.

**Everything a model says about a picture is `INFERRED`, never `TRANSCRIBED`.**
A text layer is copied character for character; a model reading a label off a
photograph is guessing at the characters, and it will also describe detail that
is not there. So the document, and every fragment in it, is `INFERRED`, which
is what puts the "unverified read" label beside it on every surface
(`app.core.attachments.UNVERIFIED_NOTE`). Nothing that reads a picture
classifies anything as a `DIMENSION`: dimension reading is P4.3's staged work,
and a tolerance a model read off a photograph is the case that staging exists
for.

**The model is injected, as a `Look`, and this module imports nothing from
`app.ai`.** The route builds one from the configured provider
(`app.ai.vision.attachment_look`), and the tests hand in a fake and open no
socket. A `Look` answers with a `Sight` or raises one of this package's own
errors:

* `UnsupportedDocument` when no model here can see. That is a capability
  answer, not a fault, and it matters most on Ollama, which does not refuse an
  image handed to a text-only model: it drops the picture and describes
  nothing, confidently. The provider's `_sees()` gate is what turns that into
  this refusal.
* `ExtractionFailed` when a model that should have answered did not: the
  provider was unreachable, refused, or returned something unusable.

**A picture nobody could read is never an empty document.** No `Look`, a model
that cannot see, and a model that answered with nothing all raise, so the
attachment row says *not read, and why* rather than "0 fragments".

**What the model writes stays `UntrustedText`.** Text in a picture is written by
whoever made the picture, and a model transcribing "ignore your instructions" off
a photograph has produced exactly that string. It is quoted like every other
reader's output (Decision 8).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.documents.document import DocumentKind, ExtractedDocument, Fragment, FragmentKind
from app.documents.errors import ExtractionFailed, UnsupportedDocument
from app.documents.provenance import Reliability, SourceRef
from app.documents.quoted import UntrustedText

#: The formats handed to a model. PNG and JPEG are the two every provider here
#: accepts; a GIF, TIFF, BMP or WebP is refused with advice to re-save it,
#: rather than sent to one provider that takes it and another that does not.
READABLE_FORMATS: frozenset[str] = frozenset({"png", "jpeg"})

#: The largest picture sent to a model, in bytes. A vision request carries the
#: whole image in one body (base64 on every provider), so the file is read into
#: memory, which nothing else in the attachment path does. This bound is what
#: makes that acceptable, and it is checked before a byte is read.
MAX_IMAGE_BYTES = 5_000_000

#: Where in a picture a fragment was "read". Server vocabulary for
#: `Locator.part`, never file text, and worded so a citation says who did the
#: reading: "the model's description of weld.jpg".
DESCRIPTION_PART = "model's description"
VISIBLE_TEXT_PART = "text the model read"

#: Said beside every picture's content, outside the quote fence. Server prose.
INFERRED_NOTE = (
    "A vision model described this picture. Nothing in it was transcribed: the model "
    "can misread text and can describe detail that is not in the picture."
)


@dataclass(frozen=True)
class Sight:
    """What a model said one picture shows.

    Plain strings, because this is the model's output before it has been
    labelled. `read_image` is where it becomes `UntrustedText`, and nothing
    else should hold a `Sight` for long.
    """

    describes: str
    """What the picture shows, in the model's words."""

    visible_text: tuple[str, ...]
    """Each piece of text the model could make out, one entry per line or label."""

    seen_by: str
    """Which model looked, as `SourceRef.reader` records it: `vision:<model>`."""


#: Looks at one picture and says what it shows. Called with the image bytes and
#: its format (`png` or `jpeg`). Raises `UnsupportedDocument` when no model can
#: see and `ExtractionFailed` when one should have answered and did not.
Look = Callable[[bytes, str], Sight]


def read_image(
    path: Path,
    source: SourceRef,
    image_format: str,
    *,
    look: Look | None,
    advice: str,
    max_fragments: int,
) -> ExtractedDocument:
    """A picture as fragments: the model's description, then each line of text it read.

    `advice` is what `kinds.sniff` said to do with a picture nothing can read,
    and it is the refusal when `look` is `None`.
    """
    if look is None or image_format not in READABLE_FORMATS:
        raise UnsupportedDocument(f"{source.filename}: {advice}")

    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ExtractionFailed(
            f"{source.filename} is {size / 1e6:.1f} MB, over the "
            f"{MAX_IMAGE_BYTES / 1e6:.0f} MB limit for a picture sent to a model, so "
            "nothing was read from it. Attach a smaller copy, or crop it to the part "
            "that matters.",
            short="too large to describe",
        )

    try:
        seen = look(path.read_bytes(), image_format)
    except UnsupportedDocument as exc:
        raise UnsupportedDocument(f"{source.filename}: {exc}") from exc
    except ExtractionFailed as exc:
        raise ExtractionFailed(f"{source.filename}: {exc}", short=exc.short) from exc

    describes = seen.describes.strip()
    lines = [line.strip() for line in seen.visible_text if line.strip()]
    if not describes and not lines:
        # The failure this package is arranged around: an answer with nothing in
        # it rendered as "0 fragments", which reads as a picture with nothing to
        # say rather than as a read that did not happen.
        raise ExtractionFailed(
            f"{source.filename}: the vision model ({seen.seen_by}) answered with no "
            "description and no text, so nothing was read from the picture. Describe "
            "what it shows, or attach it again to have it read a second time.",
            short="the model described nothing",
        )

    document_source = source.read_by(seen.seen_by, Reliability.INFERRED)
    fragments: list[Fragment] = []
    notes: list[str] = [INFERRED_NOTE]

    if describes:
        fragments.append(
            Fragment(
                text=UntrustedText(describes, document_source.at(part=DESCRIPTION_PART)),
                kind=FragmentKind.PROSE,
            )
        )
    for number, line in enumerate(lines, start=1):
        if len(fragments) >= max_fragments:
            notes.append(
                f"Stopped after {max_fragments} fragments; the rest of the text the "
                "model read was not kept."
            )
            break
        fragments.append(
            Fragment(
                text=UntrustedText(
                    line, document_source.at(part=VISIBLE_TEXT_PART, line=number)
                ),
                kind=FragmentKind.ANNOTATION,
            )
        )

    return ExtractedDocument(
        source=document_source,
        kind=DocumentKind.IMAGE,
        fragments=tuple(fragments),
        notes=tuple(notes),
    )


__all__ = [
    "DESCRIPTION_PART",
    "INFERRED_NOTE",
    "MAX_IMAGE_BYTES",
    "READABLE_FORMATS",
    "VISIBLE_TEXT_PART",
    "Look",
    "Sight",
    "read_image",
]
