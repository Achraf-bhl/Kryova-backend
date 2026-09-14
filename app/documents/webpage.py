"""A saved web page or an HTML export, read as blocks, headings and tables (P4.2).

Before this module an `.html` attachment was read by `readers._read_text` line by
line -- markup and all -- so a supplier's product page arrived as `<td
class="spec">` soup with its numbers somewhere inside. The standard library's
`html.parser` is enough to do better: it tokenises and nothing more, it fetches
nothing, runs nothing, and has no entity expansion to abuse.

What a browser would not show a person is handled on the same terms as the rest
of this package:

* `<script>`, `<style>` and `<template>` are code and layout, not words anybody
  reads, and are skipped -- with a count, so the page does not look simpler
  than it is;
* `<!-- comments -->` are not read, and counted. A comment holds no content a
  reader of the page saw, and not reading it closes the channel rather than
  hiding one: nothing from it reaches anybody;
* **text marked hidden** -- the `hidden` attribute, an inline `display:none` or
  `visibility:hidden`, `aria-hidden="true"` -- **is read, and counted.** It is
  the web's version of a PDF's `/Title`. And a stylesheet can hide text too,
  which a tokeniser cannot see, so the note says that as well rather than
  implying the count is complete;
* `<title>` and the author, description and keywords `<meta>` tags are carried
  as metadata.

Locators are the source line a block starts on, which is what "view source"
shows and the only position an HTML file itself states.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from app.documents.document import DocumentKind, ExtractedDocument, Fragment, FragmentKind
from app.documents.errors import ExtractionFailed
from app.documents.provenance import Reliability, SourceRef
from app.documents.quoted import UntrustedText
from app.documents.structure import Collector, RawCell, add_table, decode_text, strict_number

#: Elements whose contents are never text a reader of the page saw.
_SKIPPED = frozenset({"script", "style", "template", "noembed", "object"})

#: Elements that end one block of prose and start another.
_BLOCKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "caption", "dd", "details",
        "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
        "header", "hr", "html", "li", "main", "nav", "ol", "p", "pre", "section",
        "summary", "ul",
    }
)

_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

#: Void elements never get an end tag, so they must not open a hidden scope.
_VOID = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr",
    }
)

#: The `<meta name=...>` tags carried, with the label each is cited under.
_META_NAMES: dict[str, str] = {
    "author": "Author",
    "description": "Description",
    "keywords": "Keywords",
}

#: What the parser writes for `<br>`; see `_collapse`.
_BREAK = "\x00"

_HIDDEN_STYLE = re.compile(r"(display\s*:\s*none|visibility\s*:\s*hidden)", re.IGNORECASE)


def read_html(
    path: Path, source: SourceRef, *, max_fragments: int, max_text_bytes: int
) -> ExtractedDocument:
    size = path.stat().st_size
    if size > max_text_bytes:
        raise ExtractionFailed(
            f"{source.filename} is {size / 1e6:.0f} MB of HTML, over the "
            f"{max_text_bytes / 1e6:.0f} MB limit for an attachment. Save the part of the "
            "page that matters, or print it to PDF, and attach that.",
            short="too large to read as a web page",
        )

    text, encoding_note = decode_text(path.read_bytes())
    document_source = source.read_by("html.parser", Reliability.TRANSCRIBED)
    collector = Collector(limit=max_fragments, what="the page")
    if encoding_note is not None:
        collector.notes.append(encoding_note)

    parser = _PageParser(document_source, collector)
    parser.feed(text)
    parser.close()
    parser.finish()

    if parser.skipped_code:
        collector.notes.append(
            f"{parser.skipped_code} script or style block(s) were skipped -- they are code, "
            "not text a reader of the page sees."
        )
    if parser.comments:
        collector.notes.append(f"{parser.comments} HTML comment(s) were not read.")
    if parser.hidden:
        collector.notes.append(
            f"{parser.hidden} element(s) are marked hidden and their text was read anyway. "
            "Text hidden by a stylesheet cannot be told apart from visible text here."
        )

    return ExtractedDocument(
        source=document_source,
        kind=DocumentKind.HTML,
        fragments=tuple(collector.fragments),
        notes=tuple(collector.notes),
    )


@dataclass
class _Table:
    number: int
    rows: list[tuple[SourceRef, list[RawCell]]] = field(default_factory=list)
    cells: list[RawCell] | None = None
    row_line: int = 0
    cell_text: list[str] | None = None
    cell_line: int = 0
    column: int = 0
    column_span: int = 1


class _PageParser(HTMLParser):
    """Blocks of text, flushed at block boundaries, with tables kept as rows."""

    def __init__(self, source: SourceRef, collector: Collector) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.collector = collector
        self.buffer: list[str] = []
        self.buffer_line = 0
        self.buffer_kind = FragmentKind.PROSE
        self.skip_depth = 0
        self.skipped_code = 0
        self.comments = 0
        self.hidden = 0
        self.in_title = False
        self.title: list[str] = []
        self.tables: list[_Table] = []
        self.table_count = 0

    # -- tokens -----------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip_depth:
            if tag in _SKIPPED:
                self.skip_depth += 1
            return
        if tag in _SKIPPED:
            self.skip_depth = 1
            self.skipped_code += 1
            return

        attributes = {name: value or "" for name, value in attrs}
        if tag not in _VOID and (
            "hidden" in attributes
            or _HIDDEN_STYLE.search(attributes.get("style", ""))
            or attributes.get("aria-hidden", "").lower() == "true"
        ):
            self.hidden += 1

        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = attributes.get("name", "").lower()
            content = attributes.get("content", "").strip()
            if name in _META_NAMES and content:
                self._metadata(f"{_META_NAMES[name]}: {content}")
        elif tag == "br":
            self._text(_BREAK)
        elif tag == "table":
            self._flush()
            self.table_count += 1
            self.tables.append(_Table(number=self.table_count))
        elif tag == "tr" and self.tables:
            table = self.tables[-1]
            self._end_row(table)
            table.cells = []
            table.column = 0
            table.row_line = self.getpos()[0]
        elif tag in ("td", "th") and self.tables:
            table = self.tables[-1]
            if table.cells is None:
                table.cells = []
                table.row_line = self.getpos()[0]
            self._end_cell(table)
            table.column += 1
            table.cell_text = []
            table.cell_line = self.getpos()[0]
            try:
                table.column_span = max(1, int(attributes.get("colspan", "1")))
            except ValueError:
                table.column_span = 1
        elif tag in _HEADINGS:
            self._flush()
            self.buffer_kind = FragmentKind.HEADING
        elif tag in _BLOCKS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            if tag in _SKIPPED:
                self.skip_depth -= 1
            return
        if tag == "title":
            self.in_title = False
            title = "".join(self.title).strip()
            if title:
                self._metadata(f"Title: {' '.join(title.split())}")
            self.title = []
        elif tag in ("td", "th") and self.tables:
            self._end_cell(self.tables[-1])
        elif tag == "tr" and self.tables:
            self._end_row(self.tables[-1])
        elif tag == "table" and self.tables:
            self._end_table(self.tables.pop())
        elif tag in _HEADINGS:
            self._flush()
        elif tag in _BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title.append(data)
            return
        self._text(data)

    def handle_comment(self, data: str) -> None:
        self.comments += 1

    def finish(self) -> None:
        while self.tables:
            self._end_table(self.tables.pop())
        self._flush()

    # -- building ---------------------------------------------------------------

    def _text(self, data: str) -> None:
        if self.tables and self.tables[-1].cell_text is not None:
            self.tables[-1].cell_text.append(data)
            return
        if not self.buffer and data.strip():
            self.buffer_line = self.getpos()[0]
        self.buffer.append(data)

    def _flush(self) -> None:
        text = _collapse("".join(self.buffer))
        kind = self.buffer_kind
        self.buffer = []
        self.buffer_kind = FragmentKind.PROSE
        if not text or self.collector.full:
            return
        self.collector.add(
            Fragment(text=UntrustedText(text, self.source.at(line=self.buffer_line)), kind=kind)
        )

    def _metadata(self, text: str) -> None:
        self.collector.add(
            Fragment(
                text=UntrustedText(text, self.source.at(line=self.getpos()[0])),
                kind=FragmentKind.METADATA,
            )
        )

    def _end_cell(self, table: _Table) -> None:
        if table.cell_text is None or table.cells is None:
            return
        text = _collapse("".join(table.cell_text))
        column = table.column
        table.cell_text = None
        if text:
            parsed = strict_number(text)
            table.cells.append(
                RawCell(
                    column=column,
                    text=text,
                    source=self.source.at(
                        table=table.number,
                        row=len(table.rows) + 1,
                        column=column,
                        line=table.cell_line,
                    ),
                    number=parsed,
                    is_text=parsed is None,
                )
            )
        table.column += table.column_span - 1

    def _end_row(self, table: _Table) -> None:
        self._end_cell(table)
        if table.cells is None:
            return
        row_number = len(table.rows) + 1
        table.rows.append((self.source.at(table=table.number, row=row_number), table.cells))
        table.cells = None

    def _end_table(self, table: _Table) -> None:
        self._end_row(table)
        add_table(table.rows, self.collector)


def _collapse(text: str) -> str:
    """Whitespace as a browser renders it.

    A newline in the source is whitespace like any other and collapses; a `<br>`
    is a line break and survives. The two are told apart by `_BREAK`, which the
    parser writes for `<br>` and which cannot occur in the text: `kinds.sniff`
    sends nothing with a NUL byte in it to a text reader.
    """
    lines = [" ".join(part.split()) for part in text.split(_BREAK)]
    return "\n".join(line for line in lines if line).strip()


__all__ = ["read_html"]
