"""Ingesting attachments and reading them (P4 tasks 1, 3 and 4).

The layer between the readers, which are pure and complete, and the row, which
is new. Three jobs, and each of them is a rule the readers deliberately do not
hold because they have no database.

**Ingestion records an attachment even when it cannot be read.** A STEP file, a
format nothing here handles, a PDF that turns out to be a scan — every one of
them produces a row with a status and a sentence. The alternative is to refuse
the upload, which loses the fact that the user handed us something and expects
it to have been seen. `list_for` is then a complete account of what a
conversation was given, which is what the panel needs and what "first-class
object" means.

**An unsupported format is not a failure.** `UNSUPPORTED` carries the advice
`sniff` produced — "upload it as geometry for the project and it can be meshed"
— and is counted separately everywhere. Telling somebody their STEP file is
broken, when what happened is that they put geometry in the document slot, is
worse than saying nothing.

**A read that guessed is labelled, and the label travels with the content**
(P4 task 3). `Reliability.INFERRED` means the characters were guessed at rather
than transcribed, and `unverified_note` is the sentence that goes beside them:
*a wrongly read tolerance is worse than an unread one*, which is the phase's own
sentence and the reason dimension extraction is staged rather than promised.

**A fact lifted out of a document keeps its citation** (P4 task 4). `cite_fact`
turns an extracted fragment into the one-line source pointer that goes into a
parameter's description — "cell C7 of loads.xlsx, attached 2026-09-05" — so
"where did 42 mm come from" has an answer in the artefact rather than in a
transcript that trims.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.document import ExtractedDocument, Fragment, FragmentKind
from app.documents.errors import ExtractionFailed, UnsupportedDocument
from app.documents.kinds import sniff
from app.documents.provenance import SourceRef
from app.documents.readers import read_document
from app.models import Conversation, Media, User
from app.models.attachment import Attachment, ExtractionStatus

logger = logging.getLogger(__name__)

#: The sentence that goes beside content a reader *guessed* at (P4 task 3).
#: One string, in one place, because it is a safety label and a second wording
#: is a second standard.
UNVERIFIED_NOTE = (
    "Unverified read — the characters were inferred rather than transcribed. "
    "Confirm every dimension against the drawing before using it."
)

#: How many fragments are kept on the row. A five-hundred-page datasheet has
#: more, and the tail of it is not what a citation needs; `Attachment.media`
#: still holds the file, so a deeper read is a re-read rather than a loss.
#: Recorded in `extracted["truncated"]` when it bites, because a silently
#: shortened extraction reads as a short document.
MAX_STORED_FRAGMENTS = 400


@dataclass(frozen=True)
class Ingested:
    """The row and what reading it produced, if anything.

    `document` is `None` for every non-`READY` status, which is what makes the
    caller narrow before quoting rather than reaching into a half-filled object.
    """

    attachment: Attachment
    document: ExtractedDocument | None


def attach(
    db: Session,
    *,
    owner: User,
    media: Media,
    filename: str,
    path: Path,
    conversation: Conversation | None = None,
    project_id: str | None = None,
) -> Ingested:
    """Record an attachment and read it, recording whatever happened.

    Never raises for a file it cannot read. An unreadable attachment is an
    outcome the user has to be told about, and an exception here would lose the
    row that says they handed us something.
    """
    detected = sniff(path)
    attachment = Attachment(
        owner_id=owner.id,
        conversation_id=conversation.id if conversation is not None else None,
        project_id=project_id or (conversation.project_id if conversation is not None else None),
        media_id=media.id,
        filename=filename,
        detected_kind=detected.kind.value if detected.kind is not None else "unknown",
        detected_format=detected.format or "unknown",
        status=ExtractionStatus.PENDING,
    )
    db.add(attachment)
    db.flush()

    document: ExtractedDocument | None = None
    if not path.is_file():
        # Checked before the reader rather than left to it. `sniff` on a
        # missing file finds no recognisable header and `read_document` reports
        # an *unsupported format* — which is the wrong answer twice over: it
        # files a storage fault as a format one, and it tells the user to
        # "export it as PDF and attach that" about a file that is simply not
        # there.
        attachment.status = ExtractionStatus.FAILED
        attachment.status_detail = (
            f"The stored file for {filename} could not be found, so nothing was read from "
            "it. The upload may not have completed — try attaching it again."
        )
        attachment.extracted_at = datetime.now(timezone.utc)
        db.flush()
        return Ingested(attachment=attachment, document=None)

    try:
        document = read_document(
            path,
            filename=filename,
            digest=media.sha256,
            attachment_id=attachment.id,
            attached_at=attachment.created_at,
        )
    except UnsupportedDocument as exc:
        # Not a failure. The message already names what the file *is* and what
        # to do with it instead, which is the useful half.
        attachment.status = ExtractionStatus.UNSUPPORTED
        attachment.status_detail = str(exc)
    except ExtractionFailed as exc:
        attachment.status = ExtractionStatus.FAILED
        attachment.status_detail = str(exc)
    except Exception as exc:  # noqa: BLE001 - an unreadable file must not lose the row
        logger.exception("Attachment %s could not be read", attachment.id)
        attachment.status = ExtractionStatus.FAILED
        attachment.status_detail = f"This file could not be read: {exc}"
    else:
        attachment.status = ExtractionStatus.READY
        attachment.reader = document.reader
        attachment.reliability = document.reliability.value
        attachment.extracted = serialise(document)

    attachment.extracted_at = datetime.now(timezone.utc)
    db.flush()
    return Ingested(attachment=attachment, document=document)


def serialise(document: ExtractedDocument) -> dict[str, Any]:
    """`ExtractedDocument` as a dict, bounded.

    The fragment *text* is stored as plain characters, through
    `raw_for_analysis()` — the accessor `UntrustedText` provides for code that
    must actually read the payload. That is correct here and only here: this is
    *storage*, and the boundary the injection rule protects is the **render**,
    where `quote_for_user_turn` still refuses to let payload characters into
    anything but a user turn. Nothing that reads this row may put it in a system
    prompt, and nothing can: the only path from here into a model's context is
    the same quoting function every other reader goes through.
    """
    fragments = document.fragments[:MAX_STORED_FRAGMENTS]
    return {
        "kind": document.kind.value,
        "reader": document.reader,
        "reliability": document.reliability.value,
        "notes": list(document.notes),
        "truncated": len(document.fragments) > len(fragments),
        "fragment_count": len(document.fragments),
        "unread": [
            {"what": item.what, "why": item.why} for item in document.unread
        ],
        "fragments": [
            {
                "kind": fragment.kind.value,
                "text": fragment.text.raw_for_analysis(),
                "where": fragment.text.source.locator.describe(),
                "cite": fragment.text.source.cite(),
            }
            for fragment in fragments
        ],
    }


def list_for(
    db: Session, *, conversation: Conversation | None = None, owner: User | None = None
) -> Sequence[Attachment]:
    """This conversation's attachments, newest first.

    A complete account including the ones that could not be read: a panel that
    silently omitted the STEP file the user dropped in would leave them
    wondering whether it uploaded at all.
    """
    query = select(Attachment).order_by(Attachment.created_at.desc())
    if conversation is not None:
        query = query.where(Attachment.conversation_id == conversation.id)
    if owner is not None:
        query = query.where(Attachment.owner_id == owner.id)
    return list(db.scalars(query))


def unverified_note(attachment: Attachment) -> str | None:
    """The safety label for a guessed read, or `None` (P4 task 3).

    `None` rather than an empty string so a caller cannot render a blank line
    where a warning belongs and think it has handled the case.
    """
    return UNVERIFIED_NOTE if attachment.needs_confirmation else None


def dimensions_in(attachment: Attachment) -> list[dict[str, Any]]:
    """Fragments a reader classified as dimensions, with their citations.

    **Every one of these is a candidate number and nothing more.** The phase
    stages dimension and GD&T extraction as later, research-adjacent work
    explicitly *not* promised early, and this is that promise kept: the
    fragments a reader labelled `DIMENSION` are surfaced with their locators so
    a human can check them, and they are never turned into a parameter by
    anything here.
    """
    stored = (attachment.extracted or {}).get("fragments") or []
    return [
        fragment
        for fragment in stored
        if fragment.get("kind") == FragmentKind.DIMENSION.value
    ]


def cite_fact(source: SourceRef, value: str) -> str:
    """One line tying a value to where it came from (P4 task 4).

    The sentence that goes into a parameter's description when an extracted
    number becomes a design decision, so *"where did 42 mm come from"* is
    answered by the artefact rather than by a transcript that trims. The
    citation comes from `SourceRef.cite()` — one implementation, already
    sanitising the user-supplied filename — rather than being assembled here.

    An inferred read carries the warning *inside* the citation. A provenance
    line that said only "cell C7" for a number OCR guessed at would be a
    citation that made an unverified value look checked.
    """
    citation = source.cite()
    if source.reliability.needs_confirmation:
        return f"{value} — from {citation} (unverified read; confirm before use)"
    return f"{value} — from {citation}"


def fact_from_fragment(fragment: Fragment) -> str:
    """`cite_fact` for a fragment, which already carries its own `SourceRef`."""
    return cite_fact(fragment.text.source, fragment.text.raw_for_analysis())


__all__ = [
    "MAX_STORED_FRAGMENTS",
    "UNVERIFIED_NOTE",
    "Ingested",
    "attach",
    "cite_fact",
    "dimensions_in",
    "fact_from_fragment",
    "list_for",
    "serialise",
    "unverified_note",
]
