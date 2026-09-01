"""Getting text out of the source documents, whatever is installed.

PDF text extraction has no single dependency that is simultaneously fast,
accurate, pure-Python and always present, so this module does not pick one. It
tries the available extractors in order of quality and falls through, recording
which one actually ran so a puzzling index can be traced back to the extractor
that produced it.

The order is deliberate:

**`pdftotext` (poppler) first, when the binary is on PATH.** It is C++, it is
tens of times faster than any Python parser, and it reconstructs multi-column
layout properly. The reference corpus here includes single PDFs over 60 MB;
the difference between poppler and a Python parser on those is seconds against
minutes.

**`pypdf` second.** Pure Python, installs anywhere with no system package, and
is the reason this works on a machine where nobody can `apt install
poppler-utils`. Slower and weaker on multi-column pages, but correct.

**`pdfminer.six` third,** if it happens to be installed. It is the most accurate
of the three on awkward layouts and by far the slowest, so it is the last
resort rather than the default.

A file that defeats all of them is skipped with a warning and named in the build
report. One unreadable PDF must not fail a corpus build -- the other twenty are
still worth indexing, and a build that refuses everything because of one bad
input is a build nobody runs.

Extraction is **page by page** rather than whole-document, because the page
number is what a citation needs. "See the Pad dialog" is unactionable; "page 47
of the Part Design manual" sends the user to the answer.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Extensions handled without any PDF machinery. Markdown and plain text are
#: read directly, which is what makes it possible to drop a hand-written note
#: into the corpus alongside the manuals.
TEXT_SUFFIXES: frozenset[str] = frozenset({".txt", ".md", ".markdown", ".rst"})

PDF_SUFFIXES: frozenset[str] = frozenset({".pdf"})

SUPPORTED_SUFFIXES: frozenset[str] = TEXT_SUFFIXES | PDF_SUFFIXES

#: Wall-clock ceiling for one `pdftotext` invocation. A malformed PDF can send
#: poppler into a very long parse; without a timeout that stalls the whole
#: build on one file. Generous, because a legitimate 60 MB manual is slow too.
PDFTOTEXT_TIMEOUT_SECONDS: float = 300.0


@dataclass(frozen=True)
class Page:
    """One page of one document. `number` is 1-based, as a reader counts."""

    number: int
    text: str


class ExtractionError(RuntimeError):
    """No installed extractor could read this file.

    Carries two messages on purpose. `str(exc)` is the full diagnostic naming
    every extractor tried and why each failed -- what a developer needs when a
    build produces nothing. `short` is the one-clause version for the build
    report, which lists every skipped file and would otherwise repeat the same
    three paragraphs of installation advice per file.
    """

    def __init__(self, message: str, *, short: str) -> None:
        super().__init__(message)
        self.short = short


def _pdftotext_available() -> bool:
    return shutil.which("pdftotext") is not None


def _extract_with_pdftotext(path: Path) -> list[Page]:
    """Shell out to poppler, splitting on the form feed it emits per page.

    `-layout` preserves column structure, which matters for the tabular
    parameter listings these manuals are full of: without it a two-column table
    interleaves into nonsense that indexes as nonsense.
    """
    completed = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        timeout=PDFTOTEXT_TIMEOUT_SECONDS,
        check=True,
    )
    body = completed.stdout.decode("utf-8", errors="replace")
    # Poppler separates pages with \f. A trailing one produces an empty final
    # element, which is dropped by the emptiness filter in `extract_pages`.
    return [Page(number=index + 1, text=text) for index, text in enumerate(body.split("\f"))]


def _extract_with_pypdf(path: Path) -> list[Page]:
    import pypdf  # noqa: PLC0415 - optional dependency, imported where used

    reader = pypdf.PdfReader(str(path))
    pages: list[Page] = []
    for index, page in enumerate(reader.pages):
        try:
            pages.append(Page(number=index + 1, text=page.extract_text() or ""))
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            logger.debug("pypdf could not read page %d of %s", index + 1, path.name)
    return pages


def _extract_with_pdfminer(path: Path) -> list[Page]:
    from pdfminer.high_level import extract_text  # noqa: PLC0415 - optional

    body = extract_text(str(path)) or ""
    return [Page(number=index + 1, text=text) for index, text in enumerate(body.split("\f"))]


#: Name and callable for each extractor, in the order they are tried. Kept as
#: data rather than a chain of `if` statements so the build report can name the
#: extractor that ran, and so a test can drive one directly.
_PDF_EXTRACTORS: tuple[tuple[str, object], ...] = (
    ("pdftotext", _extract_with_pdftotext),
    ("pypdf", _extract_with_pypdf),
    ("pdfminer", _extract_with_pdfminer),
)


def available_extractors() -> list[str]:
    """Which PDF extractors this machine can actually use, best first.

    Used by the setup check and the build report, so "why did my corpus index
    to nothing" has an answer that names the missing package.
    """
    found: list[str] = []
    if _pdftotext_available():
        found.append("pdftotext")
    for module, name in (("pypdf", "pypdf"), ("pdfminer.high_level", "pdfminer")):
        try:
            __import__(module)
        except ImportError:
            continue
        found.append(name)
    return found


def extract_pages(path: Path) -> tuple[list[Page], str]:
    """Read `path` into pages, returning them and the extractor's name.

    Raises `ExtractionError` when nothing could read it -- the caller decides
    whether to skip the file or fail the build, and for a corpus build the
    answer is always to skip and report.
    """
    suffix = path.suffix.lower()

    if suffix in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        return [Page(number=1, text=text)], "text"

    if suffix not in PDF_SUFFIXES:
        raise ExtractionError(
            f"{path.name}: unsupported file type {suffix!r}. "
            f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}.",
            short=f"unsupported type {suffix}",
        )

    failures: list[str] = []
    for name, extractor in _PDF_EXTRACTORS:
        if name == "pdftotext" and not _pdftotext_available():
            failures.append("pdftotext: not on PATH")
            continue
        try:
            pages = [page for page in extractor(path) if page.text.strip()]  # type: ignore[operator]
        except ImportError:
            failures.append(f"{name}: not installed")
            continue
        except subprocess.TimeoutExpired:
            failures.append(f"{name}: timed out after {PDFTOTEXT_TIMEOUT_SECONDS:.0f}s")
            continue
        except Exception as exc:  # noqa: BLE001 - try the next extractor
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            continue

        if pages:
            return pages, name
        # An extractor that returns nothing has not necessarily failed -- a
        # scanned PDF genuinely has no text layer -- but the next extractor may
        # do better, so keep going rather than accepting the empty result.
        failures.append(f"{name}: no text layer found")

    # Every extractor that *ran* found no text layer, as opposed to erroring or
    # being absent: the file is a scan of paper, and the fix is OCR rather than
    # installing anything. Worth distinguishing, because "install pypdf" is
    # useless advice for an image-only PDF and sends people down a dead end.
    #
    # Only the extractors that ran get a vote. Including the absent ones made
    # this permanently false -- "pdfminer: not installed" is not a statement
    # about the document, and with any extractor uninstalled the diagnosis
    # never fired at all.
    ran = [
        failure
        for failure in failures
        if "not installed" not in failure and "not on PATH" not in failure
    ]
    scanned = bool(ran) and all("no text layer" in failure for failure in ran)
    raise ExtractionError(
        f"Could not read {path.name}. Tried: {'; '.join(failures)}. "
        + (
            "Every extractor found no text layer, so this is almost certainly a "
            "scanned document; it needs OCR before it can be indexed."
            if scanned
            else "Install poppler-utils (`pdftotext`) or `pip install pypdf`."
        ),
        short="scanned, no text layer" if scanned else "no extractor could read it",
    )
