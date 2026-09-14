"""Word documents and PowerPoint presentations, read from their XML (master plan P4.2).

A `.docx` or `.pptx` is a zip of XML parts joined by relationship files, and this
module reads those parts directly with the standard library. **That is a
decision, and it is taken against two libraries on purpose:**

* `python-docx` iterates `Document.paragraphs`, which is the body's *direct*
  paragraph children -- so a paragraph inside a content control (`w:sdt`), the
  thing form-like engineering templates are built from, is not in the list, and
  nothing says so. It also has no answer to "was this text deleted under track
  changes", which is the difference between what a document says and what it
  used to say.
* Docling and MarkItDown (the plan's first choice) both produce *Markdown*. A
  locator per paragraph, slide and table cell is the requirement (P4.4), and
  flattening first is how provenance is lost -- `document.py` says so. Docling's
  standard install also pulls in torch, torchvision, transformers and an OCR
  model (measured from its 2.127.0 metadata), which is a GPU-sized dependency for
  reading a Word file.

Walking the XML makes every place a document keeps words a decision written
down here rather than a library's default:

* **tracked deletions are not read** -- they are no longer the document's text --
  and are counted in a note; **tracked insertions are read**, and counted;
* **hidden text (`w:vanish`) is read and counted**, because a document can carry
  words its reader never sees, and dropping them would hide exactly the channel
  an injection uses;
* **a text box is read once.** Word writes each one twice, as DrawingML inside
  `mc:Choice` and as VML inside `mc:Fallback`; the fallback is never read;
* **headers, footers, footnotes, endnotes, comments and speaker notes are read**
  and located as such (`Locator.part`), and **document properties** are carried
  as metadata for the reason `readers._pdf_metadata` carries a PDF's;
* **macros, embedded objects and pictures are named in notes**, never opened.

Package-level guards -- the unpacked-size budget, the DTD refusal -- live in
`structure.py` and apply before any part is parsed.
"""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.documents.document import DocumentKind, ExtractedDocument, Fragment, FragmentKind
from app.documents.errors import ExtractionFailed
from app.documents.provenance import Reliability, SourceRef
from app.documents.quoted import UntrustedText
from app.documents.structure import (
    MAX_PACKAGE_BYTES,
    Collector,
    RawCell,
    add_table,
    open_package,
    package_properties,
    read_part,
    relationship_ids,
    relationships,
    strict_number,
    unread_package_contents,
)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

#: The namespace ISO/IEC 29500 *Strict* uses for WordprocessingML. Recognised
#: only so a Strict file is refused by name rather than read as an empty one.
_STRICT_MARKER = "purl.oclc.org/ooxml"

#: Parts of a Word document read after the body, by relationship type, with the
#: word a citation uses for each.
_WORD_PARTS: dict[str, str] = {
    "header": "header",
    "footer": "footer",
    "footnotes": "footnotes",
    "endnotes": "endnotes",
    "comments": "comments",
}

#: Element subtrees never read for text. `mc:Fallback` is the second copy of
#: something `mc:Choice` already holds; `w:del` and `w:moveFrom` are text the
#: document no longer contains; the property blocks hold formatting, and a
#: `w:vanish` inside a *paragraph's* properties hides the paragraph mark, not
#: any text.
_PRUNED = frozenset({f"{_MC}Fallback", f"{_W}del", f"{_W}moveFrom", f"{_W}pPr", f"{_W}sectPr"})

_HEADING_NAME = re.compile(r"(heading [1-9]|title)")

#: `w:outlineLvl` runs 0-8 for outline levels; 9 is "body text".
_OUTLINE_LEVELS = frozenset(str(level) for level in range(9))


@dataclass
class _Revisions:
    """What a Word document holds that a plain read would misstate, counted."""

    inserted: int = 0
    deleted: int = 0
    hidden: int = 0

    def notes(self) -> list[str]:
        found: list[str] = []
        if self.deleted:
            found.append(
                f"The document has {self.deleted} tracked deletion(s). The deleted text "
                "was not read -- it is no longer part of the document."
            )
        if self.inserted:
            found.append(
                f"The document has {self.inserted} tracked insertion(s) not yet accepted. "
                "They were read as part of the text."
            )
        if self.hidden:
            found.append(
                f"{self.hidden} run(s) of hidden text -- formatted not to show or print -- "
                "were read anyway. A document can carry words its reader never sees."
            )
        return found


# -- Word ---------------------------------------------------------------------


def read_word(path: Path, source: SourceRef, *, max_fragments: int) -> ExtractedDocument:
    """The body, then headers, footers, notes and comments, then what was not read."""
    document_source = source.read_by("ooxml", Reliability.TRANSCRIBED)
    collector = Collector(limit=max_fragments, what="the document")
    revisions = _Revisions()

    archive = open_package(path, source, max_bytes=MAX_PACKAGE_BYTES)
    try:
        package_properties(archive, document_source, collector)
        main = _main_part(archive, source)
        root = read_part(archive, main, source)
        if root is None or not root.tag.startswith(_W):
            raise _not_this_format(source, root, "Word document", ".docx")

        headings = _heading_styles(archive, main, source)
        body = root.find(f"{_W}body")
        if body is not None:
            _WordPart(document_source, collector, headings, revisions).read(body)

        for kind, target in relationships(archive, main, source):
            label = _WORD_PARTS.get(kind)
            if label is None or collector.full:
                continue
            part = read_part(archive, target, source)
            if part is None:
                continue
            reader = _WordPart(document_source.at(part=label), collector, headings, revisions)
            if kind == "comments":
                reader.read_comments(part)
            else:
                reader.read(part)

        collector.notes.extend(revisions.notes())
        unread_package_contents(archive, collector, root=posixpath.dirname(main) or "word")
    finally:
        archive.close()

    return ExtractedDocument(
        source=document_source,
        kind=DocumentKind.OFFICE,
        fragments=tuple(collector.fragments),
        notes=tuple(collector.notes),
    )


@dataclass
class _WordPart:
    """One part of a Word document -- the body, a header, the footnotes.

    Paragraphs and tables are numbered within the part, so "the footer,
    paragraph 1" and "table 2, row 3" each point at one place.
    """

    source: SourceRef
    collector: Collector
    headings: frozenset[str]
    revisions: _Revisions
    paragraphs: int = 0
    tables: int = 0

    def read(self, container: ET.Element) -> None:
        for child in container:
            if self.collector.full:
                return
            if child.tag == f"{_W}p":
                self._paragraph(child)
            elif child.tag == f"{_W}tbl":
                self._table(child)
            elif child.tag not in _PRUNED:
                # Content controls, custom XML, `mc:Choice`, a footnote's own
                # element: containers whose paragraphs are the document's.
                self.read(child)

    def read_comments(self, part: ET.Element) -> None:
        """Each comment as one annotation, with its author, in file order."""
        for comment in part.iter(f"{_W}comment"):
            text = "\n".join(
                line
                for paragraph in _paragraphs_in(comment)
                if (line := _word_text(paragraph, self.revisions)).strip()
            )
            if not text.strip():
                continue
            self.paragraphs += 1
            author = comment.get(f"{_W}author")
            body = f"Comment by {author}: {text}" if author else f"Comment: {text}"
            if not self.collector.add(
                Fragment(
                    text=UntrustedText(body, self.source.at(paragraph=self.paragraphs)),
                    kind=FragmentKind.ANNOTATION,
                )
            ):
                return

    def _paragraph(self, paragraph: ET.Element) -> None:
        text = _word_text(paragraph, self.revisions)
        if text.strip():
            self.paragraphs += 1
            kind = FragmentKind.HEADING if self._is_heading(paragraph) else FragmentKind.PROSE
            if not self.collector.add(
                Fragment(
                    text=UntrustedText(text, self.source.at(paragraph=self.paragraphs)),
                    kind=kind,
                )
            ):
                return
        # A text box anchored in this paragraph: its own paragraphs, read after
        # the one that holds it, never merged into it.
        for box in _text_boxes(paragraph):
            self.read(box)

    def _table(self, table: ET.Element) -> None:
        self.tables += 1
        number = self.tables
        rows: list[tuple[SourceRef, list[RawCell]]] = []
        for row_number, row in enumerate(_children(table, f"{_W}tr"), start=1):
            cells: list[RawCell] = []
            column = 1
            for cell in _children(row, f"{_W}tc"):
                span = _grid_span(cell)
                text = "\n".join(
                    line
                    for paragraph in _paragraphs_in(cell)
                    if (line := _word_text(paragraph, self.revisions)).strip()
                )
                if text.strip():
                    parsed = strict_number(text)
                    cells.append(
                        RawCell(
                            column=column,
                            text=text,
                            source=self.source.at(table=number, row=row_number, column=column),
                            number=parsed,
                            is_text=parsed is None,
                        )
                    )
                column += span
            rows.append((self.source.at(table=number, row=row_number), cells))
        add_table(rows, self.collector)

    def _is_heading(self, paragraph: ET.Element) -> bool:
        properties = paragraph.find(f"{_W}pPr")
        if properties is None:
            return False
        outline = properties.find(f"{_W}outlineLvl")
        if outline is not None and outline.get(f"{_W}val") in _OUTLINE_LEVELS:
            return True
        style = properties.find(f"{_W}pStyle")
        return style is not None and style.get(f"{_W}val") in self.headings


def _word_text(paragraph: ET.Element, revisions: _Revisions) -> str:
    """A paragraph's text in reading order, nested paragraphs excluded."""
    out: list[str] = []
    stack = list(reversed(list(paragraph)))
    while stack:
        element = stack.pop()
        tag = element.tag
        if tag in _PRUNED:
            if tag in (f"{_W}del", f"{_W}moveFrom"):
                revisions.deleted += 1
            continue
        if tag in (f"{_W}p", f"{_W}txbxContent"):
            continue  # a text box's paragraphs are read on their own
        if tag == f"{_W}t":
            out.append(element.text or "")
        elif tag in (f"{_W}tab", f"{_W}ptab"):
            out.append("\t")
        elif tag in (f"{_W}br", f"{_W}cr"):
            out.append("\n")
        elif tag == f"{_W}noBreakHyphen":
            out.append("-")
        elif tag in (f"{_W}vanish", f"{_W}specVanish"):
            if element.get(f"{_W}val") not in ("0", "false", "off"):
                revisions.hidden += 1
        elif tag in (f"{_W}ins", f"{_W}moveTo"):
            revisions.inserted += 1
        stack.extend(reversed(list(element)))
    return "".join(out)


def _text_boxes(paragraph: ET.Element) -> list[ET.Element]:
    """The `w:txbxContent` elements anchored in a paragraph, fallbacks excluded."""
    found: list[ET.Element] = []
    stack = list(reversed(list(paragraph)))
    while stack:
        element = stack.pop()
        if element.tag in _PRUNED:
            continue
        if element.tag == f"{_W}txbxContent":
            found.append(element)
            continue
        stack.extend(reversed(list(element)))
    return found


def _paragraphs_in(container: ET.Element) -> list[ET.Element]:
    """Every paragraph under a table cell or comment, fallbacks excluded."""
    found: list[ET.Element] = []
    stack = list(reversed(list(container)))
    while stack:
        element = stack.pop()
        if element.tag in _PRUNED:
            continue
        if element.tag == f"{_W}p":
            found.append(element)
        stack.extend(reversed(list(element)))
    return found


def _children(element: ET.Element, tag: str) -> list[ET.Element]:
    """Children with `tag`, looking through content-control and custom-XML wrappers."""
    found: list[ET.Element] = []
    for child in element:
        if child.tag == tag:
            found.append(child)
        elif child.tag in (f"{_W}sdt", f"{_W}sdtContent", f"{_W}customXml"):
            found.extend(_children(child, tag))
    return found


def _grid_span(cell: ET.Element) -> int:
    span = cell.find(f"{_W}tcPr/{_W}gridSpan")
    try:
        return max(1, int(span.get(f"{_W}val", "1"))) if span is not None else 1
    except ValueError:
        return 1


def _heading_styles(archive: zipfile.ZipFile, main: str, source: SourceRef) -> frozenset[str]:
    """Style ids whose style is a heading, followed through `w:basedOn`.

    **By the style's name, never its id.** The id is localised -- a French Word
    writes `Titre1` for what an English one writes `Heading1` -- while the name
    of a built-in style is the English `heading 1` on every install.
    """
    styles_part = next(
        (target for kind, target in relationships(archive, main, source) if kind == "styles"),
        None,
    )
    if styles_part is None:
        return frozenset()
    root = read_part(archive, styles_part, source)
    if root is None:
        return frozenset()

    direct: dict[str, bool] = {}
    parent: dict[str, str] = {}
    for style in root.iter(f"{_W}style"):
        style_id = style.get(f"{_W}styleId")
        if style_id is None or style.get(f"{_W}type") not in (None, "paragraph"):
            continue
        name_element = style.find(f"{_W}name")
        name = (name_element.get(f"{_W}val", "") if name_element is not None else "").lower()
        outline = style.find(f"{_W}pPr/{_W}outlineLvl")
        direct[style_id] = bool(_HEADING_NAME.fullmatch(name)) or (
            outline is not None and outline.get(f"{_W}val") in _OUTLINE_LEVELS
        )
        based_on = style.find(f"{_W}basedOn")
        if based_on is not None and based_on.get(f"{_W}val"):
            parent[style_id] = based_on.get(f"{_W}val", "")

    headings: set[str] = set()
    for style_id in direct:
        current: str | None = style_id
        for _ in range(16):  # a basedOn cycle is malformed; stop rather than spin
            if current is None:
                break
            if direct.get(current):
                headings.add(style_id)
                break
            current = parent.get(current)
    return frozenset(headings)


# -- PowerPoint -----------------------------------------------------------------


def read_presentation(path: Path, source: SourceRef, *, max_fragments: int) -> ExtractedDocument:
    """Every slide in show order, its tables and speaker notes, then what was not read."""
    document_source = source.read_by("ooxml", Reliability.TRANSCRIBED)
    collector = Collector(limit=max_fragments, what="the presentation")

    archive = open_package(path, source, max_bytes=MAX_PACKAGE_BYTES)
    try:
        package_properties(archive, document_source, collector)
        main = _main_part(archive, source)
        root = read_part(archive, main, source)
        if root is None or root.tag != f"{_P}presentation":
            raise _not_this_format(source, root, "PowerPoint presentation", ".pptx")

        ids = relationship_ids(archive, main, source)
        order = [
            ids[slide_id.get(_R_ID, "")]
            for slide_id in root.iter(f"{_P}sldId")
            if slide_id.get(_R_ID, "") in ids
        ]

        hidden: list[int] = []
        commented: list[int] = []
        unread_graphics = 0
        for number, slide_part in enumerate(order, start=1):
            if collector.full:
                break
            slide = read_part(archive, slide_part, source)
            if slide is None:
                collector.notes.append(
                    f"Slide {number} is listed in the presentation but its part is missing, "
                    "so nothing was read from it."
                )
                continue
            if slide.get("show") in ("0", "false"):
                hidden.append(number)

            reader = _SlideReader(document_source.at(slide=number), collector)
            tree = slide.find(f"{_P}cSld/{_P}spTree")
            if tree is not None:
                reader.read(tree)
            unread_graphics += reader.unread_graphics

            for kind, target in relationships(archive, slide_part, source):
                if kind == "notesSlide":
                    notes = read_part(archive, target, source)
                    notes_tree = notes.find(f"{_P}cSld/{_P}spTree") if notes is not None else None
                    if notes_tree is not None:
                        notes_reader = _SlideReader(
                            document_source.at(slide=number, part="speaker notes"),
                            collector,
                            notes_page=True,
                        )
                        notes_reader.read(notes_tree)
                        unread_graphics += notes_reader.unread_graphics
                elif kind == "comments":
                    comments = read_part(archive, target, source)
                    if comments is not None and _read_slide_comments(
                        comments, document_source.at(slide=number, part="comments"), collector
                    ):
                        commented.append(number)

        if hidden:
            collector.notes.append(
                f"Slide(s) {_listed(hidden)} are hidden in the presentation and were read "
                "anyway -- they are not shown when it is presented."
            )
        if unread_graphics:
            collector.notes.append(
                f"{unread_graphics} chart(s) or diagram(s) were not read. Their text and "
                "numbers are held in separate parts; attach the source data if it matters."
            )
        unread_package_contents(archive, collector, root=posixpath.dirname(main) or "ppt")
    finally:
        archive.close()

    return ExtractedDocument(
        source=document_source,
        kind=DocumentKind.OFFICE,
        fragments=tuple(collector.fragments),
        notes=tuple(collector.notes),
    )


#: Placeholders on a notes page that the program fills in -- the slide's
#: picture, its number, the date. Everything else on the page is read.
_GENERATED_NOTES_PLACEHOLDERS = frozenset({"sldImg", "sldNum", "dt"})


@dataclass
class _SlideReader:
    """The shapes of one slide, or of its notes page, in the order they are stored.

    `notes_page` skips the placeholders a notes page fills in by itself
    (`_GENERATED_NOTES_PLACEHOLDERS`) and reads **every other shape** -- not only
    the `body` placeholder PowerPoint puts the notes in. A first version read the
    body placeholder alone, and a LibreOffice export whose notes sat in an
    ordinary text box came back with no notes and no remark: the silent drop this
    package exists to refuse, on the one part of a deck nobody sees presented.
    """

    source: SourceRef
    collector: Collector
    notes_page: bool = False
    paragraphs: int = 0
    tables: int = 0
    unread_graphics: int = 0

    def read(self, tree: ET.Element) -> None:
        for shape in tree:
            if self.collector.full:
                return
            tag = shape.tag
            if tag == f"{_P}sp":
                self._shape(shape)
            elif tag == f"{_P}grpSp":
                self.read(shape)
            elif tag == f"{_P}graphicFrame":
                table = shape.find(f".//{_A}tbl")
                if table is not None:
                    self._table(table)
                else:
                    self.unread_graphics += 1
            elif tag == f"{_MC}AlternateContent":
                choice = shape.find(f"{_MC}Choice")
                if choice is not None:
                    self.read(choice)

    def _shape(self, shape: ET.Element) -> None:
        placeholder = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
        kind_of_placeholder = placeholder.get("type") if placeholder is not None else None
        if self.notes_page and kind_of_placeholder != "body":
            return
        heading = kind_of_placeholder in ("title", "ctrTitle")
        body = shape.find(f"{_P}txBody")
        if body is None:
            return
        for paragraph in body.findall(f"{_A}p"):
            text = _drawing_text(paragraph)
            if not text.strip():
                continue
            self.paragraphs += 1
            if not self.collector.add(
                Fragment(
                    text=UntrustedText(text, self.source.at(paragraph=self.paragraphs)),
                    kind=FragmentKind.HEADING if heading else FragmentKind.PROSE,
                )
            ):
                return

    def _table(self, table: ET.Element) -> None:
        self.tables += 1
        number = self.tables
        rows: list[tuple[SourceRef, list[RawCell]]] = []
        for row_number, row in enumerate(table.findall(f"{_A}tr"), start=1):
            cells: list[RawCell] = []
            for column, cell in enumerate(row.findall(f"{_A}tc"), start=1):
                if cell.get("hMerge") in ("1", "true") or cell.get("vMerge") in ("1", "true"):
                    continue  # the merged-into cell holds the text
                text = "\n".join(
                    line
                    for paragraph in cell.findall(f"{_A}txBody/{_A}p")
                    if (line := _drawing_text(paragraph)).strip()
                )
                if text.strip():
                    parsed = strict_number(text)
                    cells.append(
                        RawCell(
                            column=column,
                            text=text,
                            source=self.source.at(table=number, row=row_number, column=column),
                            number=parsed,
                            is_text=parsed is None,
                        )
                    )
            rows.append((self.source.at(table=number, row=row_number), cells))
        add_table(rows, self.collector)


def _drawing_text(paragraph: ET.Element) -> str:
    """A DrawingML paragraph's runs, fields and line breaks, in order."""
    out: list[str] = []
    for child in paragraph:
        if child.tag in (f"{_A}r", f"{_A}fld"):
            text = child.find(f"{_A}t")
            out.append((text.text or "") if text is not None else "")
        elif child.tag == f"{_A}br":
            out.append("\n")
    return "".join(out)


def _read_slide_comments(part: ET.Element, source: SourceRef, collector: Collector) -> bool:
    """A slide's comments as annotations -- legacy `p:text` and 2018 `a:p` forms alike."""
    found = False
    for index, comment in enumerate(
        (element for element in part.iter() if element.tag.endswith("}cm")), start=1
    ):
        legacy = comment.find(f"{_P}text")
        if legacy is not None and legacy.text:
            text = legacy.text
        else:
            text = "\n".join(
                line for p in comment.iter(f"{_A}p") if (line := _drawing_text(p)).strip()
            )
        if not text.strip():
            continue
        found = True
        collector.add(
            Fragment(
                text=UntrustedText(f"Comment: {text}", source.at(paragraph=index)),
                kind=FragmentKind.ANNOTATION,
            )
        )
    return found


# -- shared ---------------------------------------------------------------------


def _main_part(archive: zipfile.ZipFile, source: SourceRef) -> str:
    target = next(
        (t for kind, t in relationships(archive, "", source) if kind == "officeDocument"), None
    )
    if target is None:
        raise ExtractionFailed(
            f"{source.filename} is a zip that says it is an Office file but names no main "
            "document part. Open it and save it again, then attach that.",
            short="the Office package is damaged",
        )
    return target


def _not_this_format(
    source: SourceRef, root: ET.Element | None, what: str, suffix: str
) -> ExtractionFailed:
    if root is not None and _STRICT_MARKER in root.tag:
        return ExtractionFailed(
            f"{source.filename} is saved as Strict Open XML, which is not read here. Save "
            f"it again as an ordinary {what} ({suffix}) and attach that.",
            short="Strict Open XML is not read",
        )
    return ExtractionFailed(
        f"{source.filename} does not contain a readable {what}. Open it and save it again "
        f"as {suffix}, then attach that.",
        short="the Office package is damaged",
    )


def _listed(numbers: list[int]) -> str:
    return ", ".join(str(number) for number in numbers[:12]) + (" …" if len(numbers) > 12 else "")


__all__ = ["read_presentation", "read_word"]
