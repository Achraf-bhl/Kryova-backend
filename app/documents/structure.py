"""What the table, Office and web-page readers share, so they share one answer.

Four things every structured reader needs and none of them should decide twice:

* **a fragment budget that says when it bit** (`Collector`) -- the same
  `MAX_FRAGMENTS` rule `readers.py` keeps for PDF and DXF, with the note that
  stops a cut extraction reading as a short document;
* **rows of cells, with a heading row taken or not** (`add_table`) -- one rule
  for a spreadsheet, a Word table and an HTML table, so "which value was under
  `Fx [N]`" has the same answer whichever of the three the user sent;
* **text that is not UTF-8** (`decode_text`) -- Excel on Windows writes a CSV in
  Windows-1252 by default, and a `°C` read as a replacement character is a unit
  silently lost;
* **an Office package opened with a budget** (`open_package`, `read_part`) --
  a `.docx` is a zip of XML, so it can be a zip bomb or carry a DTD, and both
  are refused before a parser is pointed at them.

Nothing here reads a format. It is the vocabulary the readers are written in.
"""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.documents.document import Cell, Fragment, FragmentKind, Unread
from app.documents.errors import ExtractionFailed
from app.documents.provenance import SourceRef
from app.documents.quoted import UntrustedText

#: The declared uncompressed size of an Office package above which it is not
#: opened. A zip's member sizes are declared in its directory, and `zipfile`
#: checks each member's CRC against what it inflated, so a member that lies
#: about its size fails rather than inflating past it -- which makes the sum of
#: the declared sizes an honest ceiling on what reading the package can cost.
#: 25 MB of XML is a spreadsheet of a few hundred thousand cells or a Word file
#: of thousands of pages; a load-case table is kilobytes.
MAX_PACKAGE_BYTES = 25_000_000

#: A plain decimal and nothing else. No thousands separator, no decimal comma,
#: no unit, no `nan`/`inf`, no underscores -- each of those is either a guess
#: about who wrote the number or a Python spelling no spreadsheet uses.
_PLAIN_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def strict_number(text: str) -> float | None:
    """The number `text` spells, when it spells nothing else.

    `1200`, `-300.5` and `2.1e5` are numbers. `1,200` is not: it is twelve
    hundred in Manchester and one point two in Munich, and the reader has no way
    to know which -- so it is left as text rather than chosen.
    """
    stripped = text.strip()
    if not _PLAIN_NUMBER.fullmatch(stripped):
        return None
    return float(stripped)


def column_letter(index: int) -> str:
    """`1` -> `A`, `27` -> `AA`: the column as a spreadsheet names it."""
    if index < 1:
        raise ValueError(f"column index is 1-based, got {index}")
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def decode_text(data: bytes) -> tuple[str, str | None]:
    """Text, and a note when it was not UTF-8.

    UTF-8 is tried strictly first, with a byte-order mark allowed because Excel
    writes one. What is left is decoded as Windows-1252 -- which every byte
    sequence is -- and **said**, because that is the one decoding step here that
    is a guess: a file in some other single-byte encoding decodes without error
    into the wrong accented letters.
    """
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), (
            "The file is not UTF-8, so it was read as Windows-1252 -- what Excel on "
            "Windows writes by default. Check accented letters and unit symbols such "
            "as ° and µ against the original, or re-save it as CSV UTF-8."
        )


@dataclass
class Collector:
    """Fragments, refusals and notes, with the fragment budget enforced once.

    `add` returns False once the budget is spent, and the caller stops walking
    the file. The note is written by the collector the first time it refuses,
    naming `what` -- "the workbook", "the presentation" -- so a cut extraction
    says what was left unread in the words of the thing that was cut.
    """

    limit: int
    what: str
    fragments: list[Fragment] = field(default_factory=list)
    unread: list[Unread] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    full: bool = False

    def add(self, fragment: Fragment) -> bool:
        if self.full:
            return False
        if len(self.fragments) >= self.limit:
            self.full = True
            self.notes.append(
                f"Stopped after {self.limit} fragments; the rest of {self.what} was not "
                "read. Attach the part that matters on its own."
            )
            return False
        self.fragments.append(fragment)
        return True


@dataclass(frozen=True)
class RawCell:
    """A cell as a reader found it, before a heading row is decided.

    `is_text` is whether the file holds characters here rather than a typed
    value. It is what the heading rule reads: a row of words above a row with a
    number in it is a heading row, and a row that already holds a number is not.
    For a format with no types -- CSV, a Word table -- a cell is text unless
    `strict_number` reads it.
    """

    column: int
    text: str
    source: SourceRef
    number: float | None = None
    is_text: bool = True


def add_table(
    rows: Sequence[tuple[SourceRef, Sequence[RawCell]]],
    collector: Collector,
) -> SourceRef | None:
    """Every row as a `TABLE_ROW` fragment of cells. Returns the heading row, if one was taken.

    **The heading rule is stated rather than clever**, and it would rather bind
    no headings than wrong ones. Rows are scanned from the top, and the heading
    row is the first one that

    * holds only text -- scanning stops at the first row holding a number, a date
      or a boolean, because data has started; and
    * has a non-empty cell over **every column the row below it fills.**

    The second condition is what skips a title. "Load cases for the bracket" in
    A1, spanning the table, covers one column; the real headings under it cover
    all of them. Without it the title becomes the heading of column A and the
    true heading row is read as data -- measured on a Word table exported by
    LibreOffice before this rule existed. The cost is stated too: a first data
    row with a value in a column that has no heading means no headings are taken
    at all.

    Taking a heading row is a reading of the table's layout, not of any
    character in it, so it changes no reliability. Every row is still emitted --
    the heading row and anything above it with no headings -- so a wrong choice
    loses nothing. Columns are matched by position; a later cell under no
    heading has `heading=None` rather than a neighbour's.
    """
    non_empty = [(source, cells) for source, cells in rows if cells]
    heading_index: int | None = None
    for index in range(len(non_empty) - 1):
        candidate = non_empty[index][1]
        if not all(cell.is_text for cell in candidate):
            break
        covered = {cell.column for cell in candidate if cell.text.strip()}
        below = {cell.column for cell in non_empty[index + 1][1]}
        if below <= covered:
            heading_index = index
            break

    headings: dict[int, UntrustedText] = {}
    if heading_index is not None:
        headings = {
            cell.column: UntrustedText(cell.text, cell.source)
            for cell in non_empty[heading_index][1]
            if cell.text.strip()
        }

    for index, (row_source, raw_cells) in enumerate(non_empty):
        under = headings if heading_index is not None and index > heading_index else {}
        cells = tuple(
            Cell(
                text=UntrustedText(raw.text, raw.source),
                heading=under.get(raw.column),
                number=raw.number,
            )
            for raw in raw_cells
        )
        rendered = " | ".join(
            f"{under[raw.column].raw_for_analysis()}: {raw.text}"
            if raw.column in under
            else raw.text
            for raw in raw_cells
        )
        added = collector.add(
            Fragment(
                text=UntrustedText(rendered, row_source),
                kind=FragmentKind.TABLE_ROW,
                cells=cells,
            )
        )
        if not added:
            break
    return non_empty[heading_index][0] if heading_index is not None else None


# -- Office Open XML packages --------------------------------------------------

#: The relationship namespaces. Types are compared on their last path segment
#: (`header`, `slide`, `core-properties`), because Transitional, Strict and
#: Microsoft's own 2018 comment relationship all end the same way and differ
#: before it.
_PACKAGE_RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def open_package(path: Path, source: SourceRef, *, max_bytes: int) -> zipfile.ZipFile:
    """The zip, opened, once its declared size is known to be within budget.

    The caller closes it. Raises `ExtractionFailed` naming the size and what to
    do, or naming the damage when the archive will not open.
    """
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExtractionFailed(
            f"{source.filename} looks like an Office file but the package could not be "
            f"opened ({exc}). Open it and save it again, then attach that.",
            short="the Office package is damaged",
        ) from exc

    declared = sum(member.file_size for member in archive.infolist())
    if declared > max_bytes:
        archive.close()
        raise ExtractionFailed(
            f"{source.filename} unpacks to {declared / 1e6:.0f} MB, over the "
            f"{max_bytes / 1e6:.0f} MB this reader opens. Attach the sheet, section "
            "or slides that matter on their own, or export them as PDF.",
            short="too large to unpack",
        )
    return archive


def read_part(archive: zipfile.ZipFile, name: str, source: SourceRef) -> ET.Element | None:
    """One XML part of the package, or None when the package has no such part.

    **A DTD is refused before parsing.** Office Open XML parts have no business
    declaring one, and a DTD is how an XML file defines the entities an
    expansion attack is made of. The expat bundled with CPython bounds that
    expansion; refusing the declaration outright means this reader does not
    rest on which expat a deployment happened to link.
    """
    try:
        data = archive.read(name)
    except KeyError:
        return None
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise ExtractionFailed(
            f"{source.filename}: the part {name} could not be unpacked ({exc}). "
            "Open the file and save it again, then attach that.",
            short="the Office package is damaged",
        ) from exc

    # The keyword is case-sensitive in XML, and a `<` in text content is always
    # escaped, so the literal bytes appear only where a DTD is declared.
    if b"<!DOCTYPE" in data:
        raise ExtractionFailed(
            f"{source.filename}: the part {name} declares a DTD, which no Office program "
            "writes. The file was not read. If it came from a trusted source, open it "
            "and save it again, then attach that.",
            short="refused: the file declares a DTD",
        )
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ExtractionFailed(
            f"{source.filename}: the part {name} is not well-formed XML ({exc}). "
            "Open the file and save it again, then attach that.",
            short="the Office package is damaged",
        ) from exc


def relationships(archive: zipfile.ZipFile, part: str, source: SourceRef) -> list[tuple[str, str]]:
    """`(type, target part)` for every internal relationship of `part`, in file order.

    `part` is `""` for the package itself. External targets -- a hyperlink, a
    linked image on somebody's share -- are not parts of this file and are left
    out; nothing here fetches anything.
    """
    directory, filename = posixpath.split(part)
    rels_name = posixpath.join(directory, "_rels", f"{filename}.rels")
    root = read_part(archive, rels_name, source)
    if root is None:
        return []
    found: list[tuple[str, str]] = []
    for rel in root.iter(f"{_PACKAGE_RELS}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target") or ""
        kind = (rel.get("Type") or "").rstrip("/").rsplit("/", 1)[-1]
        if target.startswith("/"):
            resolved = target.lstrip("/")
        else:
            resolved = posixpath.normpath(posixpath.join(directory, target))
        found.append((kind, resolved))
    return found


def relationship_ids(archive: zipfile.ZipFile, part: str, source: SourceRef) -> dict[str, str]:
    """`{rId: target part}` for `part` -- how a presentation names its slides."""
    directory, filename = posixpath.split(part)
    root = read_part(archive, posixpath.join(directory, "_rels", f"{filename}.rels"), source)
    if root is None:
        return {}
    ids: dict[str, str] = {}
    for rel in root.iter(f"{_PACKAGE_RELS}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target") or ""
        rel_id = rel.get("Id")
        if rel_id is None:
            continue
        ids[rel_id] = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join(directory, target))
        )
    return ids


_CORE = {
    "{http://purl.org/dc/elements/1.1/}title": "Title",
    "{http://purl.org/dc/elements/1.1/}subject": "Subject",
    "{http://purl.org/dc/elements/1.1/}creator": "Author",
    "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}keywords": "Keywords",
    "{http://purl.org/dc/elements/1.1/}description": "Description",
    "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}lastModifiedBy": (
        "Last modified by"
    ),
}

_CUSTOM_PROPERTY = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/custom-properties}property"
)


def package_properties(
    archive: zipfile.ZipFile, source: SourceRef, collector: Collector
) -> None:
    """The document properties, as `METADATA` fragments.

    The Office equivalent of a PDF's information dictionary, and carried for the
    same reason `readers._pdf_metadata` carries that: a title or a custom
    property is written by whoever made the file, shows on no page, and is the
    cheapest place in it to hide a line addressed to a model. Quoted and
    located, rather than dropped where nobody notices it is there.
    """
    where = source.at(part="properties")
    for kind, target in relationships(archive, "", source):
        if kind == "core-properties":
            root = read_part(archive, target, source)
            if root is None:
                continue
            for element in root:
                label = _CORE.get(element.tag)
                value = (element.text or "").strip()
                if label is not None and value:
                    collector.add(
                        Fragment(
                            text=UntrustedText(f"{label}: {value}", where),
                            kind=FragmentKind.METADATA,
                        )
                    )
        elif kind == "custom-properties":
            root = read_part(archive, target, source)
            if root is None:
                continue
            for prop in root.iter(_CUSTOM_PROPERTY):
                name = prop.get("name") or ""
                value = "".join(child.text or "" for child in prop).strip()
                if name and value:
                    collector.add(
                        Fragment(
                            text=UntrustedText(f"{name}: {value}", where),
                            kind=FragmentKind.METADATA,
                        )
                    )


def unread_package_contents(archive: zipfile.ZipFile, collector: Collector, *, root: str) -> None:
    """Notes for what a package carries that no reader here opens.

    Macros, embedded files and pictures. The embedded file is the one that
    matters most: a load table pasted into a Word document *as an Excel object*
    is a whole workbook inside the file, and reading the Word text around it
    while saying nothing would present the document as complete.
    """
    names = archive.namelist()
    if any(name.startswith(f"{root}/") and name.endswith("vbaProject.bin") for name in names):
        collector.notes.append(
            "The file contains macros. They were not run and not read."
        )
    embedded = [name for name in names if name.startswith(f"{root}/embeddings/")]
    if embedded:
        collector.notes.append(
            f"{len(embedded)} embedded file(s) -- a spreadsheet or drawing pasted in as "
            "an object -- were not read. Attach them separately if they matter."
        )
    pictures = [name for name in names if name.startswith(f"{root}/media/")]
    if pictures:
        collector.notes.append(
            f"{len(pictures)} picture(s) were not read. Describe any that matter, or "
            "attach them on their own."
        )


__all__ = [
    "MAX_PACKAGE_BYTES",
    "Collector",
    "RawCell",
    "add_table",
    "column_letter",
    "decode_text",
    "open_package",
    "package_properties",
    "read_part",
    "relationship_ids",
    "relationships",
    "strict_number",
    "unread_package_contents",
]
