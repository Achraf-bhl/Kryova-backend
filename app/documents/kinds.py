"""Deciding what a file *is*, from its bytes rather than from its name.

The extension is a claim made by whoever named the file, and this package's
whole premise is that whoever named the file is not to be trusted. A `.txt` that
is really a PDF, a `.pdf` that is really a zip, a `.step` that is really a shell
script -- none of those is exotic, and routing on the name alone hands the
attacker the choice of parser. So the bytes decide, and the extension is used
only where the bytes genuinely cannot distinguish (a `.docx` and an `.xlsx` are
both zips -- though even there the zip's *entry names* settle it, so the
extension is a tiebreak of last resort).

**Only the head of the file is read.** `sniff` takes at most `SNIFF_BYTES`, and
`_zip_family` reads a zip's central directory rather than its contents. Nothing
here loads a whole file into memory -- the same rule `app.media` keeps, for the
same reason: a 400 MB STEP attachment must cost a few kilobytes to classify.

Geometry is not re-detected here. `app.geometry.formats.detect_format` already
owns the question of which solid formats the mesher can open, including the
carefully-worded rejections for `.CATPart` and friends, so a solid CAD file is
recognised and handed straight on rather than described twice in two places
that could disagree.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.documents.document import DocumentKind
from app.geometry.formats import detect_format

#: How much of the file the sniffer reads. Enough for every magic number below
#: and for a DXF's leading `0/SECTION` pair with generous whitespace, small
#: enough to be free.
SNIFF_BYTES = 4096


@dataclass(frozen=True)
class Detection:
    """What the file is, and -- when nothing can read it -- what to do instead."""

    kind: DocumentKind
    format: str
    """A specific label: `pdf`, `xlsx`, `dxf`, `step`, `png`, `text`. Used to
    pick a reader and shown to the user, so it is a word, not a MIME type."""

    advice: str | None = None
    """Set when this deployment cannot read the format, phrased as the next
    action. `None` means something here can read it."""


_PDF = b"%PDF-"
_ZIP = b"PK\x03\x04"
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_STEP = b"ISO-10303-21"
_DXF_BINARY = b"AutoCAD Binary DXF"
_DWG = b"AC10"

_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"BM", "bmp"),
)

#: Images are recognised so the user is told what to do, not so they are read.
#: P4.2 routes photographs to the vision provider; that is `app/ai/vision.py`'s
#: job and not this package's, and until an attachment reaches it the honest
#: answer is that no text was extracted.
_IMAGE_ADVICE = (
    "Images are not read as text here. Describe what the picture shows, or "
    "attach the drawing as a PDF or DXF."
)

#: Zip entry prefixes that identify an Office Open XML package. Checked against
#: the archive's own directory, so a `.xlsx` renamed to `.docx` still routes to
#: the spreadsheet reader.
_OOXML_MARKERS: tuple[tuple[str, DocumentKind, str], ...] = (
    ("xl/", DocumentKind.SPREADSHEET, "xlsx"),
    ("word/", DocumentKind.OFFICE, "docx"),
    ("ppt/", DocumentKind.OFFICE, "pptx"),
)

_TABULAR_SUFFIXES = frozenset({".csv", ".tsv"})
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".rst", ".log", ".json", ".xml"})


def sniff(path: Path) -> Detection:
    """Classify `path` by its content, falling back to its name only when tied.

    Never raises for an unreadable or nonsense file: an unrecognised blob comes
    back as `DocumentKind.UNKNOWN` with advice, because the caller's job is to
    tell the user what to convert it to, not to handle an exception.
    """
    path = Path(path)
    try:
        with path.open("rb") as handle:
            head = handle.read(SNIFF_BYTES)
    except OSError as exc:
        return Detection(
            DocumentKind.UNKNOWN,
            "unreadable",
            advice=f"The file could not be opened ({exc.strerror or exc}). Re-upload it.",
        )

    if not head:
        return Detection(
            DocumentKind.UNKNOWN, "empty", advice="The file is empty -- there is nothing to read."
        )

    if head.startswith(_PDF):
        return Detection(DocumentKind.PDF, "pdf")

    if head.startswith(_ZIP):
        return _zip_family(path)

    if head.startswith(_OLE2):
        return Detection(
            DocumentKind.OFFICE,
            "ole2",
            advice=(
                "This is a legacy binary Office file (.doc/.xls/.ppt), which is not read "
                "here. Open it and re-save as .docx/.xlsx/.pptx, or export to PDF."
            ),
        )

    if head.startswith(_STEP) or b"ISO-10303-21" in head[:512]:
        return Detection(DocumentKind.CAD_SOLID, "step")

    if head.startswith(_DXF_BINARY):
        return Detection(
            DocumentKind.DXF,
            "dxf",
            advice=(
                "This is a binary DXF. Re-save it from AutoCAD as an ASCII DXF "
                "(Save As > AutoCAD ASCII DXF) and attach that."
            ),
        )

    if head.startswith(_DWG):
        return Detection(
            DocumentKind.UNKNOWN,
            "dwg",
            advice=(
                "AutoCAD .dwg is a closed binary format and is not read here. "
                "In AutoCAD use Save As > AutoCAD ASCII DXF and attach the .dxf."
            ),
        )

    # WEBP is `RIFF????WEBP`, so it is matched on the second marker rather than
    # on the bare `RIFF`, which would claim every wav and avi file as an image.
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return Detection(DocumentKind.IMAGE, "webp", advice=_IMAGE_ADVICE)
    for magic, name in _IMAGE_MAGIC:
        if head.startswith(magic):
            return Detection(DocumentKind.IMAGE, name, advice=_IMAGE_ADVICE)

    if _looks_like_ascii_dxf(head):
        return Detection(DocumentKind.DXF, "dxf")

    geometry = detect_format(path.name)
    if geometry is not None:
        # STL and IGES have no magic worth trusting -- an ASCII STL begins
        # `solid <anything>` and a binary one begins with 80 arbitrary bytes --
        # so for these the extension is all there is, and the mesher will reject
        # a fake at import.
        return Detection(DocumentKind.CAD_SOLID, geometry)

    suffix = path.suffix.lower()
    if suffix in _TABULAR_SUFFIXES and _is_texty(head):
        return Detection(DocumentKind.SPREADSHEET, suffix.lstrip("."))

    if _is_texty(head):
        label = suffix.lstrip(".") if suffix in _TEXT_SUFFIXES else "text"
        return Detection(DocumentKind.PLAIN_TEXT, label)

    return Detection(
        DocumentKind.UNKNOWN,
        "binary",
        advice=(
            "This does not look like any format read here (PDF, DXF, STEP/IGES/STL, "
            "spreadsheet, or plain text). Export it as PDF and attach that."
        ),
    )


def _zip_family(path: Path) -> Detection:
    """Tell the Office Open XML packages apart by the archive's own directory.

    `zipfile` reads the end-of-central-directory record and the directory
    itself, not the member data, so this stays cheap on a large `.xlsx`.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return Detection(
            DocumentKind.UNKNOWN,
            "zip",
            advice="The file begins like a zip archive but could not be opened. Re-save it.",
        )

    for prefix, kind, label in _OOXML_MARKERS:
        if any(name.startswith(prefix) for name in names):
            return Detection(kind, label)

    return Detection(
        DocumentKind.UNKNOWN,
        "zip",
        advice=(
            "This is a plain zip archive. Attach the individual files rather "
            "than the archive."
        ),
    )


def _looks_like_ascii_dxf(head: bytes) -> bool:
    """An ASCII DXF opens with group code 0 followed by `SECTION`.

    Checked as a pair rather than by searching for `SECTION` anywhere, because
    the word turns up in ordinary prose and this is the branch that decides a
    text file gets handed to a CAD parser.
    """
    try:
        text = head.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return len(lines) >= 2 and lines[0] == "0" and lines[1] == "SECTION"


def _is_texty(head: bytes) -> bool:
    """Whether the head decodes as text with no NUL bytes.

    A NUL is the reliable signal of a binary file: it cannot appear in UTF-8
    text, and every format above that is not caught by a magic number and is not
    text has one within the first few kilobytes.
    """
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        # A truncated multi-byte sequence at the sniff boundary is not evidence
        # of a binary file; retry without the tail.
        try:
            head[:-4].decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True
