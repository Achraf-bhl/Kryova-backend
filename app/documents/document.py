"""What an extracted document is: fragments, each with a place and a reader.

An `ExtractedDocument` is deliberately *not* a blob of markdown. Markdown is
what a chat window wants; what the rest of Kryova wants is a sequence of pieces
each of which knows where it came from, because that is the only shape from
which "cell C7 of loads.xlsx" can still be said three steps downstream (master
plan P4.4). Flattening to prose first and trying to recover the page number
afterwards is how provenance gets lost.

**A document says what it could not read.** `unread` is not an error list -- an
extraction with fifty good fragments and three unreadable ones is a success that
must not pretend to be complete. A drawing with a dimension the parser declined
is exactly the case the honesty rule in this package exists for: naming the line
it refused is worth more than a plausible number.

`fragments` is ordered as the reader walked the file -- reading order for a PDF,
sheet then row for a spreadsheet, entity order for a DXF. Nothing here sorts by
relevance; ranking belongs to whoever consumes it.
"""

from __future__ import annotations

import enum
from collections.abc import Iterator
from dataclasses import dataclass, field

from app.documents.provenance import Reliability, SourceRef
from app.documents.quoted import UntrustedText


class FragmentKind(enum.Enum):
    """What a fragment *is*, so a consumer can pick without re-parsing.

    The distinctions are the ones that change how content should be read, not a
    taxonomy for its own sake. Prose is read as prose; a table row has columns
    that mean something; a dimension label on a drawing is a candidate number
    and nothing else; a layer name or a document title is metadata that is
    still, emphatically, user-written text.
    """

    PROSE = "prose"
    TABLE_ROW = "table_row"
    HEADING = "heading"
    DIMENSION = "dimension"
    ANNOTATION = "annotation"
    METADATA = "metadata"


class DocumentKind(enum.Enum):
    """The family a file belongs to, decided from its bytes (`kinds.py`)."""

    PLAIN_TEXT = "plain_text"
    PDF = "pdf"
    SPREADSHEET = "spreadsheet"
    OFFICE = "office"
    DXF = "dxf"
    CAD_SOLID = "cad_solid"
    IMAGE = "image"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Fragment:
    """One piece of a document, with its provenance and its kind.

    `text` carries the `SourceRef`, so a fragment lifted out of a document on its
    own is still fully cited. That redundancy is deliberate: fragments get
    filtered, sorted and passed around, and a citation that lived only on the
    parent would be lost at the first list comprehension.
    """

    text: UntrustedText
    kind: FragmentKind = FragmentKind.PROSE

    @property
    def source(self) -> SourceRef:
        return self.text.source


@dataclass(frozen=True)
class Unread:
    """Something present in the file that was deliberately not interpreted.

    Not an exception -- the extraction succeeded. This is the parser saying "I
    saw a number here, I could not decide what it meant, and I would rather tell
    you than guess". `where` locates it, `what` quotes enough to find it and
    `why` says what would resolve it, in the register the house rule asks for:
    the reader should know what to change.
    """

    where: SourceRef
    what: str
    why: str

    def describe(self) -> str:
        return f"{self.where.cite()}: {self.why} (saw: {self.what!r})"


@dataclass(frozen=True)
class ExtractedDocument:
    """Everything one attachment yielded, and everything it did not."""

    source: SourceRef
    """Document-level provenance: filename, digest, reader, reliability."""

    kind: DocumentKind
    fragments: tuple[Fragment, ...] = ()
    unread: tuple[Unread, ...] = ()

    notes: tuple[str, ...] = field(default_factory=tuple)
    """Reader-level remarks a user should see -- "3 sheets, 1 was empty",
    "this PDF has no text layer on pages 4-9". Server-authored prose, not file
    content, so it is safe to render outside the fence."""

    @property
    def reader(self) -> str:
        return self.source.reader

    @property
    def reliability(self) -> Reliability:
        return self.source.reliability

    @property
    def is_empty(self) -> bool:
        return not self.fragments

    def texts(self, *kinds: FragmentKind) -> Iterator[UntrustedText]:
        """The quoted text of every fragment, optionally filtered by kind."""
        for fragment in self.fragments:
            if not kinds or fragment.kind in kinds:
                yield fragment.text

    def summary(self) -> str:
        """A line for the attachment list. Never contains file content.

        The filename is the user's own text, so it is *not* interpolated here --
        `SourceRef.cite()` is for citations rendered at a boundary that
        sanitises. This is the count, which is server-computed and safe.
        """
        parts = [f"{self.kind.value}", f"{len(self.fragments)} fragment(s)"]
        if self.unread:
            parts.append(f"{len(self.unread)} not interpreted")
        parts.append(f"read by {self.reader}")
        if self.reliability.needs_confirmation:
            parts.append("unverified read")
        return ", ".join(parts)
