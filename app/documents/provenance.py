"""Where a piece of extracted content came from, and how much to believe it.

Master plan **P4.4**: *"Where did 42 mm come from?" must answer "cell C7 of
loads.xlsx, attached 2026-09-05"*. That sentence is this module's whole job. A
`SourceRef` is what makes it answerable, and it travels with every fragment of
text and every candidate fact this package produces -- there is no type here
that carries content without one, which is deliberate: an extracted number with
no provenance is a rumour.

**Reliability is a fact about how the characters were obtained, never about
whether the meaning is right.** The vocabulary mirrors `app.kernel.provenance`
because the two answer the same question one layer apart, and one vocabulary is
easier to reason about than two:

* `MEASURED` -- read out of a structured field that means exactly this. A DXF
  `DIMENSION` entity's own measurement, a spreadsheet cell's numeric value.
  These are the only ones where the file itself asserts the number.
* `TRANSCRIBED` -- copied character-exact from a text layer. The characters are
  right; nothing has been claimed about what they *mean*. Most PDF content.
* `INFERRED` -- produced by OCR, or by a heuristic parse of prose. The
  characters themselves may be wrong. Master plan P4.3 is explicit that this
  tier is labelled "unverified read -- confirm before use", because a
  misread tolerance is worse than an unread one.
* `UNAVAILABLE` -- nothing could be read, and `reason` says why.

Nothing in this package ever promotes a value up this scale. A number parsed out
of prose in a PDF is `INFERRED` even when it looks unambiguous, because
"looks unambiguous" is exactly the judgement that produces a confidently wrong
tolerance.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any


class Reliability(enum.Enum):
    """How the characters were obtained. Never a claim about their meaning."""

    MEASURED = "measured"
    TRANSCRIBED = "transcribed"
    INFERRED = "inferred"
    UNAVAILABLE = "unavailable"

    @property
    def needs_confirmation(self) -> bool:
        """True when a human must check this before it is acted on.

        `INFERRED` is the whole of it today. Kept as a property rather than
        written out at each call site so that adding an OCR tier later cannot
        miss one.
        """
        return self is Reliability.INFERRED


@dataclass(frozen=True)
class Locator:
    """Where inside a file something sits, in the units that file has.

    Every field is optional because the answer differs per format and inventing
    a page number for a DXF layer would be worse than leaving it out. `describe`
    renders only what is actually known, so a citation never contains
    "page None".
    """

    page: int | None = None
    """1-based, as a reader counts. PDFs, and anything paginated."""

    sheet: str | None = None
    """Worksheet name. Spreadsheets."""

    cell: str | None = None
    """A1-style reference. Spreadsheets."""

    row: int | None = None
    """1-based row, for tabular content with no cell reference."""

    line: int | None = None
    """1-based line within the extracted text of the page or file."""

    layer: str | None = None
    """DXF layer name. Untrusted text in its own right -- see `quoted`."""

    entity: str | None = None
    """DXF entity handle, or another format's internal element id."""

    def describe(self) -> str:
        """The location, in the words an engineer would use to find it again."""
        parts: list[str] = []
        if self.sheet is not None:
            parts.append(f'sheet "{self.sheet}"')
        if self.cell is not None:
            parts.append(f"cell {self.cell}")
        elif self.row is not None:
            parts.append(f"row {self.row}")
        if self.page is not None:
            parts.append(f"page {self.page}")
        if self.layer is not None:
            parts.append(f'layer "{self.layer}"')
        if self.entity is not None:
            parts.append(f"entity {self.entity}")
        if self.line is not None:
            parts.append(f"line {self.line}")
        return ", ".join(parts)


@dataclass(frozen=True)
class SourceRef:
    """The full provenance of one piece of extracted content.

    `digest` is the durable identity, not `filename`. Two users attach the same
    datasheet under two names and the content-addressed store holds one blob
    (`app.media.LocalMediaStore`); a citation that keyed on the name would claim
    they were different documents. The filename is carried because it is what
    the user recognises -- and it is *their* text, so it is untrusted and gets
    sanitised wherever it is rendered.
    """

    filename: str
    """As uploaded. Untrusted user-supplied text; never rendered raw."""

    digest: str | None = None
    """SHA-256 of the blob in `app.media`, when the content came from the store."""

    attachment_id: str | None = None
    """The attachment row's id, once P4.1 has a table. None for a bare path."""

    locator: Locator = Locator()

    reader: str = "unknown"
    """Which extractor produced this -- `pypdf`, `ezdxf`, `openpyxl`, `docling`.

    Named so that a puzzling extraction can be traced back to the code that made
    it, exactly as `app.retrieval.extract` returns the extractor's name.
    """

    reliability: Reliability = Reliability.TRANSCRIBED

    attached_at: datetime | None = None
    """When the user attached it, for the citation. None when unrecorded."""

    reason: str | None = None
    """Why nothing could be read. Only meaningful with `UNAVAILABLE`."""

    def at(self, **locator_fields: Any) -> SourceRef:
        """A copy of this ref pointing at a more specific place in the same file.

        Readers build one document-level ref and narrow it per page, per row,
        per entity. Doing that with `replace` at every call site loses the
        nesting; this keeps the fields already set and overrides only what is
        given.

        The values are `Any` because `Locator`'s fields are genuinely of
        different types and `**kwargs` gives mypy one type for all of them —
        a union would make every field claim to accept every other field's type,
        which is weaker than no annotation rather than stronger. `replace`
        validates the real field types at the call.
        """
        narrowed = replace(self.locator, **locator_fields)
        return replace(self, locator=narrowed)

    def read_by(self, reader: str, reliability: Reliability) -> SourceRef:
        """A copy naming the extractor that actually ran and what that implies."""
        return replace(self, reader=reader, reliability=reliability)

    def cite(self) -> str:
        """One line an engineer can act on, answering "where did this come from".

        The filename is *not* sanitised here -- this returns a plain string and
        the sanitising belongs at the boundary that renders it, which is
        `quoted.quote_for_user_turn` for the model and the API schema for the
        UI. Doing it twice would double-defang a legitimate filename containing
        a bracket.
        """
        where = self.locator.describe()
        parts = [f"{where} of {self.filename}" if where else self.filename]
        if self.attached_at is not None:
            parts.append(f"attached {self.attached_at.date().isoformat()}")
        if self.digest:
            parts.append(f"sha256 {self.digest[:12]}")
        line = ", ".join(parts)
        if self.reliability.needs_confirmation:
            line += " -- unverified read, confirm before use"
        if self.reliability is Reliability.UNAVAILABLE and self.reason:
            line += f" -- not read: {self.reason}"
        return line
