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

**An attached part becomes geometry through one function** (P4.2).
`geometry_version_from` is called by `POST /projects/{id}/geometry/from-attachment`
and by the agent's `import_geometry_from_attachment` tool, so the button and the
assistant cannot disagree about which attachments are parts. Both callers
authorise the project first; this checks the attachment.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.documents.document import Cell, ExtractedDocument, Fragment, FragmentKind
from app.documents.errors import ExtractionFailed, UnsupportedDocument
from app.documents.images import Look
from app.documents.kinds import sniff
from app.documents.provenance import Locator, Reliability, SourceRef
from app.documents.quoted import ToolResultBlock, UntrustedText, quote_for_tool_result
from app.documents.readers import read_document
from app.geometry.formats import GEOMETRY_FORMATS
from app.geometry.inspect import inspect
from app.models import Conversation, GeometryVersion, Media, Project, User
from app.models.attachment import Attachment, ExtractionStatus

if TYPE_CHECKING:
    from app.media import MediaService

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
    look: Look | None = None,
) -> Ingested:
    """Record an attachment and read it, recording whatever happened.

    Never raises for a file it cannot read. An unreadable attachment is an
    outcome the user has to be told about, and an exception here would lose the
    row that says they handed us something.

    `look` is the model a PNG or JPEG is described by (`app.documents.images`).
    Without one a picture is `UNSUPPORTED`, saying so; with one that cannot
    see, it is `UNSUPPORTED` with the provider's reason; with one that did not
    answer, `FAILED`. It is never `READY` with nothing in it.
    """
    detected = sniff(path, filename)
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
            look=look,
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
        # `where` was missing until P4.2 gave a workbook something to refuse per
        # cell: "a formula with no saved result" is no use without "cell B3".
        "unread": [
            {"what": item.what, "why": item.why, "where": item.where.locator.describe()}
            for item in document.unread
        ],
        "fragments": [
            {
                "kind": fragment.kind.value,
                "text": fragment.text.raw_for_analysis(),
                "where": fragment.text.source.locator.describe(),
                # The structured locator beside the rendered one. `where` is for
                # a human and cannot be parsed back — `cell C7` and
                # `sheet "Loads", cell C7` are both strings and neither is a
                # `Locator`. P4.7 needs the fields themselves twice: to rebuild
                # the `SourceRef` whose header cites a quoted fragment, and so
                # `read_attachment` can be *addressed* by locator rather than by
                # position in a list that a re-read may reorder.
                "locator": _locator(fragment.text.source.locator),
                "cite": fragment.text.source.cite(),
                "cells": [_cell(cell) for cell in fragment.cells],
            }
            for fragment in fragments
        ],
    }


def _locator(locator: Locator) -> dict[str, Any]:
    """The locator's set fields, and only those.

    Written out rather than `asdict`ed so a row does not carry eleven nulls per
    fragment, and so adding a field to `Locator` is a decision here rather than
    a silent change to every stored attachment.
    """
    return {
        field.name: value
        for field in dataclass_fields(locator)
        if (value := getattr(locator, field.name)) is not None
    }


def _cell(cell: Cell) -> dict[str, Any]:
    """One table cell as stored: its value, its column heading, and where each was read.

    The heading is stored as text beside the value rather than by reference to
    the heading row, so a row lifted out of the list still says which column
    each value was under. `reliability` is per cell because it differs within a
    row -- a typed number is `measured` and the label beside it `transcribed`.
    """
    return {
        "text": cell.text.raw_for_analysis(),
        "heading": cell.heading.raw_for_analysis() if cell.heading is not None else None,
        "number": cell.number,
        "reliability": cell.source.reliability.value,
        "where": cell.source.locator.describe(),
        "cite": cell.source.cite(),
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


def stored_fragments(attachment: Attachment) -> list[UntrustedText]:
    """This attachment's extracted fragments, as quotable untrusted text (P4.7).

    The inverse of `serialise`, and the only way a stored fragment becomes
    something a model may be shown: it comes back as `UntrustedText` carrying
    its own `SourceRef`, so the one route onward is `quote_for_user_turn`. A
    caller that wanted the characters would have to write
    `raw_for_analysis`, which `tests/test_documents_injection.py` refuses
    outside `app/documents`.

    A row written before P4.7 has no structured `locator`, only the rendered
    `where`. Its fragments still quote, and their header degrades to the
    filename, the reader and the reliability. That is a citation that says less,
    which is the acceptable half of the trade; the alternative is parsing
    `sheet "Loads", cell C7` back into fields, and a citation reconstructed by
    guesswork would be a citation that is sometimes wrong.
    """
    if not attachment.status.readable:
        return []
    stored = (attachment.extracted or {}).get("fragments") or []
    base = SourceRef(
        filename=attachment.filename,
        digest=attachment.sha256,
        attachment_id=attachment.id,
        reader=attachment.reader,
        reliability=_reliability(attachment.reliability),
        attached_at=attachment.created_at,
    )
    quotable: list[UntrustedText] = []
    for fragment in stored:
        text = fragment.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        quotable.append(UntrustedText(text, base.at(**_locator_fields(fragment))))
    return quotable


def _locator_fields(fragment: dict[str, Any]) -> dict[str, Any]:
    """The stored locator, keyed only by fields `Locator` actually declares.

    A row is JSONB and an older one may hold a key this build has dropped;
    `replace` would raise on it and lose the whole turn's quoting over one
    stale fragment. Unknown keys are ignored rather than fatal.
    """
    stored = fragment.get("locator")
    if not isinstance(stored, dict):
        return {}
    known = {field.name for field in dataclass_fields(Locator)}
    return {key: value for key, value in stored.items() if key in known}


def _reliability(value: str) -> Reliability:
    """The stored reliability as its enum, defaulting to the cautious answer.

    An unrecognised value becomes `INFERRED` rather than `TRANSCRIBED`, so a
    row this build cannot interpret is quoted *with* the unverified-read
    warning. Guessing in the safe direction is the whole of the rule.
    """
    try:
        return Reliability(value)
    except ValueError:
        return Reliability.INFERRED


def owned(db: Session, *, attachment_id: str, owner: User) -> Attachment:
    """This user's attachment, or `AttachmentNotFound`.

    One answer for "no such id" and "somebody else's", so ids cannot be probed
    — the rule `geometry_version_from` already applies inline, lifted out so the
    reading tool cannot accidentally choose a laxer one. **Owner, not
    membership:** an attachment is a file a person handed over, and a
    conversation shared with an organisation does not make one member's
    uploaded datasheet readable by another. `_writable_project`'s membership
    rule is about writing into a shared project, which is a different question.
    """
    attachment = db.get(Attachment, attachment_id)
    if attachment is None or attachment.owner_id != owner.id:
        raise AttachmentNotFound(attachment_id)
    return attachment


def read_fragments(
    attachment: Attachment,
    *,
    where: str | None = None,
    contains: str | None = None,
    offset: int = 0,
    limit: int = 40,
) -> ReadResult:
    """Fragments of one attachment, selected and quoted for a tool result (P4.7).

    The way back to anything the turn's budget left out. Three ways to say which
    part, all optional and combinable:

    * `where` matches the rendered locator -- `"C7"`, `"sheet \"Loads\""`,
      `"slide 3"` -- case-insensitively, as a substring. A substring rather than
      a structured query because the model has *seen* these strings: every quoted
      fragment's header carries one, so the natural way to ask for more of what
      it just read is to repeat what it was shown.
    * `contains` matches the fragment's own text. Matching payload characters is
      a *search*, not a render: nothing that matches leaves this module except
      through the quoting boundary below.
    * `offset` and `limit` page through what is left, so a datasheet can be read
      in sections rather than in one refused request.

    The selection is by locator and content and never by list position alone,
    because a re-read may reorder the list -- `serialise` stores the reader's
    order, and a reader upgrade can change it. `offset` pages a *filtered*
    result and is stated as such in the reply.
    """
    everything = stored_fragments(attachment)
    matched = [
        item
        for item in everything
        if _matches(item, where=where, contains=contains)
    ]
    window = matched[offset : offset + max(1, limit)]
    return ReadResult(
        block=quote_for_tool_result(window),
        matched=len(matched),
        total=len(everything),
        offset=offset,
        returned=len(window),
    )


@dataclass(frozen=True)
class ReadResult:
    """What `read_fragments` found, and how much of it it is handing back.

    The counts are separate from the block so a caller can tell the model *"12
    matched, 5 shown"* without opening the payload. `block` is the only member
    carrying characters from the file, and its own type governs where they may
    go.
    """

    block: ToolResultBlock
    matched: int
    total: int
    offset: int
    returned: int


def _matches(item: UntrustedText, *, where: str | None, contains: str | None) -> bool:
    """Does this fragment answer the request?

    `raw_for_analysis` here is a read for *matching*, which is what the accessor
    is for: the result is a boolean, and no path from this function returns
    characters. The rendering is `quote_for_tool_result`'s and only its.
    """
    if where:
        described = item.source.locator.describe()
        if where.casefold() not in described.casefold():
            return False
    if contains:
        if contains.casefold() not in item.raw_for_analysis().casefold():
            return False
    return True


def inventory_line(attachment: Attachment) -> str:
    """One line naming an attachment and what reading it produced (P4.7).

    Sent every turn, unlike the content, which is sent on the turn after it was
    attached. The transcript window trims, so an attachment quoted once and then
    trimmed would leave the agent with no way to know the file exists; this line
    is a few dozen characters and keeps it knowable. It is deliberately *about*
    the file and never from it — the filename is the only user-supplied part,
    and `quote_for_user_turn` sanitises it with everything else.

    A `FAILED` or `UNSUPPORTED` attachment is named here with its reason. The
    alternative — silence — makes the product look as though it ignored the file,
    which is the question P4.1's status vocabulary exists to answer.
    """
    parts = [f"{attachment.filename!r} ({attachment.detected_format})"]
    if attachment.status is ExtractionStatus.READY:
        count = len((attachment.extracted or {}).get("fragments") or [])
        parts.append(f"read by {attachment.reader}, {count} fragment(s)")
        if attachment.needs_confirmation:
            parts.append(UNVERIFIED_NOTE)
        if ((attachment.extracted or {}).get("truncated")) is True:
            total = (attachment.extracted or {}).get("fragment_count")
            parts.append(
                f"only the first {count} of {total} fragments were stored"
                if total
                else "not every fragment was stored"
            )
    else:
        parts.append(attachment.status.value)
        if attachment.status_detail:
            parts.append(attachment.status_detail)
    return f"- id {attachment.id}: " + " — ".join(parts)


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


class AttachmentNotFound(LookupError):
    """No attachment with this id belongs to the caller.

    One answer for "does not exist" and "is somebody else's", so an id cannot be
    probed. The route renders it as 404 and the agent's tool as "not found".
    """


class NotSolidGeometry(ValueError):
    """The attachment was read as something other than a part. The message names what."""


def is_solid_geometry(attachment: Attachment) -> bool:
    """Was this attachment detected as a STEP, IGES or STL part?

    Detection read the bytes when the file was attached, so a `.step` that is
    really a text file was recorded as text and is not a part here.
    """
    return attachment.detected_format in GEOMETRY_FORMATS


def geometry_version_from(
    db: Session,
    media: MediaService,
    *,
    owner: User,
    project: Project,
    attachment_id: str,
    note: str | None = None,
) -> GeometryVersion:
    """Make an attached part a geometry version of `project`, with no second upload.

    **The caller has already authorised `project` for writing.** The route does it
    with `OwnedProject` and the tool with the same membership rule; this checks
    only that the attachment is the caller's own. `Attachment.project_id` is not
    consulted, because a label on an attachment grants nothing.

    The version and the attachment share one blob. Flushed, not committed: the
    caller commits, or rolls back when it has a reason to.

    Raises `AttachmentNotFound`, `NotSolidGeometry`, and whatever inspection
    raises for a file that does not read as the part it claims to be
    (`GeometryError`) or is no longer stored (`MediaNotFound`). **A failed
    inspection deletes nothing.** The upload routes discard an unreadable CAD
    blob, but this one is under an attachment the user can still see.
    """
    attachment = db.get(Attachment, attachment_id)
    if attachment is None or attachment.owner_id != owner.id:
        raise AttachmentNotFound(attachment_id)
    if not is_solid_geometry(attachment):
        *others, last = [name.upper() for name in GEOMETRY_FORMATS]
        supported = f"{', '.join(others)} or {last}"
        raise NotSolidGeometry(
            f"This attachment was read as {attachment.detected_format}, not as solid "
            f"geometry, so it cannot become a geometry version. Attach the part as "
            f"{supported} and use that."
        )

    stored = attachment.media
    stats = inspect(media.local_path(stored), attachment.detected_format)
    highest = db.scalar(
        select(func.max(GeometryVersion.version_number)).where(
            GeometryVersion.project_id == project.id
        )
    )
    version = GeometryVersion(
        project_id=project.id,
        media_id=stored.id,
        version_number=(highest or 0) + 1,
        filename=stored.filename,
        file_format=attachment.detected_format,
        note=note,
        stats=stats,
    )
    db.add(version)
    db.flush()
    return version


__all__ = [
    "MAX_STORED_FRAGMENTS",
    "UNVERIFIED_NOTE",
    "AttachmentNotFound",
    "Ingested",
    "NotSolidGeometry",
    "attach",
    "cite_fact",
    "dimensions_in",
    "fact_from_fragment",
    "geometry_version_from",
    "is_solid_geometry",
    "list_for",
    "serialise",
    "unverified_note",
]
