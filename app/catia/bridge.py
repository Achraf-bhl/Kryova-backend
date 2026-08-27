"""CATIA V5 COM bridge (Windows-only).

Kryova's premise is that the assistant does the work, so the CAD side has to be
something the agent can drive rather than something the user hand-uploads.
This module is that seam: launch CATIA, see what is open in it, and pull the
active part out as STEP for the mesh/FEA pipeline.

Five rules shape this file.

**It imports cleanly everywhere.** `win32com` is imported lazily inside the
worker, never at module scope, so the backend still starts on Linux/macOS and
every entry point raises `CATIAUnavailableError` there instead of `ImportError`.

**Every COM call runs on its own thread that owns its apartment.** COM objects
belong to the apartment that created them and must not be shared across
threads. FastAPI runs sync handlers on an arbitrary threadpool thread, so each
call here gets a fresh thread that calls `CoInitialize`, creates its own CATIA
reference, finishes with it, and calls `CoUninitialize`. Nothing COM-shaped
escapes `_run_com`.

**Every call has a timeout.** A modal dialog in CATIA blocks COM indefinitely.
The worker thread is a daemon, so a hung call surfaces as `CATIATimeoutError`
and the process can still exit.

**Attaching and launching are different operations.** `GetActiveObject` attaches
to a running instance and raises if there is none; `Dispatch` *starts* CATIA if
it is not running. Status checks must never boot a 2 GB CAD system as a side
effect, so `is_catia_running` uses the former and only `launch` uses the latter.

**Export paths are validated, never interpolated from user input.** The format
comes from an enum and the destination is resolved under a caller-supplied
directory.
"""

from __future__ import annotations

import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, TypeVar

from app.catia.errors import (
    CATIABridgeError,
    CATIAExportError,
    CATIANotRunningError,
    CATIATimeoutError,
    CATIAUnavailableError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: CATIA is a single-instance desktop application; serialise access to it the
#: same way gmsh is serialised, so two requests cannot drive the GUI at once.
_CATIA_LOCK = threading.Lock()

DEFAULT_TIMEOUT_SECONDS = 60.0
#: Launching a cold CATIA is slow -- it is a full CAD system, not a service.
LAUNCH_TIMEOUT_SECONDS = 180.0

PROGID = "CATIA.Application"


class ExportFormat(StrEnum):
    """Formats the mesher can actually read back. Never a raw user string."""

    STEP = "stp"
    STL = "stl"
    IGES = "igs"

    @property
    def suffix(self) -> str:
        return f".{self.value}"

    @classmethod
    def parse(cls, value: str) -> "ExportFormat":
        normalised = (value or "").strip().lower().lstrip(".")
        aliases = {"step": cls.STEP, "stp": cls.STEP, "stl": cls.STL,
                   "iges": cls.IGES, "igs": cls.IGES}
        if normalised not in aliases:
            raise CATIAExportError(
                f"Unsupported export format {value!r}. "
                f"Use one of: step, stl, iges."
            )
        return aliases[normalised]


@dataclass(frozen=True)
class CatiaDocument:
    name: str
    path: str | None
    doc_type: str


@dataclass(frozen=True)
class CatiaStatus:
    """What the UI and the agent need to know, in one shot."""

    running: bool
    version: str | None = None
    document_count: int = 0
    active_document: str | None = None
    detail: str | None = None


def is_windows() -> bool:
    return sys.platform == "win32"


def _require_windows() -> None:
    if not is_windows():
        raise CATIAUnavailableError(
            "The CATIA bridge is Windows-only: it drives CATIA V5 over COM, "
            "which does not exist on this platform."
        )


def _run_com(work: Callable[[Any], T], *, timeout: float, launch: bool) -> T:
    """Run `work(catia)` on a thread that owns its own COM apartment.

    `launch=False` attaches to a running CATIA and raises `CATIANotRunningError`
    if there is none. `launch=True` starts CATIA when it is not already up.
    """
    _require_windows()

    def target() -> T:
        import pythoncom  # noqa: PLC0415  -- Windows-only, imported per worker
        import win32com.client  # noqa: PLC0415

        pythoncom.CoInitialize()
        try:
            if launch:
                catia = win32com.client.Dispatch(PROGID)
            else:
                try:
                    catia = win32com.client.GetActiveObject(PROGID)
                except Exception as exc:  # com_error and friends
                    raise CATIANotRunningError(
                        "CATIA is not running. Ask the user to start it, or call "
                        "the launch endpoint to start it for them."
                    ) from exc
            return work(catia)
        finally:
            pythoncom.CoUninitialize()

    with _CATIA_LOCK:
        # One worker per call: the apartment dies with the thread, so no COM
        # reference can leak into a later, differently-initialised thread.
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="catia-com")
        try:
            future = executor.submit(target)
            try:
                return future.result(timeout=timeout)
            except FuturesTimeout as exc:
                raise CATIATimeoutError(
                    f"CATIA did not respond within {timeout:g}s. It is usually "
                    "waiting on a dialog box -- switch to the CATIA window and "
                    "dismiss it, then try again."
                ) from exc
        finally:
            # Never join: a wedged COM call would block shutdown forever.
            executor.shutdown(wait=False)


def _version_string(catia: Any) -> str:
    config = catia.SystemConfiguration
    return f"V{config.Version}-R{config.Release}"


def is_catia_running() -> bool:
    """True when a CATIA instance exists. Never starts one."""
    if not is_windows():
        return False
    try:
        return _run_com(lambda _catia: True, timeout=15.0, launch=False)
    except (CATIANotRunningError, CATIAUnavailableError, CATIATimeoutError):
        return False


def get_status() -> CatiaStatus:
    """A never-raising status summary, so the UI can always render something."""
    if not is_windows():
        return CatiaStatus(
            running=False,
            detail="CATIA integration needs Windows; this server is not on Windows.",
        )

    def work(catia: Any) -> CatiaStatus:
        documents = catia.Documents
        count = int(documents.Count)
        active: str | None = None
        if count:
            try:
                active = str(catia.ActiveDocument.Name)
            except Exception:  # no active document is normal, not an error
                active = None
        return CatiaStatus(
            running=True,
            version=_version_string(catia),
            document_count=count,
            active_document=active,
        )

    try:
        return _run_com(work, timeout=20.0, launch=False)
    except CATIABridgeError as exc:
        return CatiaStatus(running=False, detail=str(exc))


def launch(visible: bool = True) -> CatiaStatus:
    """Start CATIA if needed and bring it on screen.

    Returns the same shape as `get_status` so a caller can render the result
    without a second round trip.
    """

    def work(catia: Any) -> CatiaStatus:
        # A COM-started CATIA comes up hidden; the whole point here is that the
        # user sees the window, so this is set explicitly every time.
        catia.Visible = bool(visible)
        return CatiaStatus(
            running=True,
            version=_version_string(catia),
            document_count=int(catia.Documents.Count),
        )

    return _run_com(work, timeout=LAUNCH_TIMEOUT_SECONDS, launch=True)


def list_open_documents() -> list[CatiaDocument]:
    def work(catia: Any) -> list[CatiaDocument]:
        documents = catia.Documents
        found: list[CatiaDocument] = []
        for index in range(1, int(documents.Count) + 1):
            doc = documents.Item(index)
            name = str(doc.Name)
            try:
                # An unsaved document has no path; that is not an error.
                path = str(doc.FullName) or None
            except Exception:
                path = None
            found.append(
                CatiaDocument(
                    name=name,
                    path=path,
                    doc_type=name.rsplit(".", 1)[-1].lower() if "." in name else "unknown",
                )
            )
        return found

    return _run_com(work, timeout=DEFAULT_TIMEOUT_SECONDS, launch=False)


def new_part(name: str | None = None) -> CatiaDocument:
    """Create an empty CATPart and show it, so the user has somewhere to model."""

    def work(catia: Any) -> CatiaDocument:
        catia.Visible = True
        doc = catia.Documents.Add("Part")
        if name:
            try:
                doc.Part.set_Name(str(name)[:60])
            except Exception:
                # Naming is cosmetic; never fail the creation over it.
                logger.debug("Could not rename the new CATIA part", exc_info=True)
        return CatiaDocument(name=str(doc.Name), path=None, doc_type="catpart")

    return _run_com(work, timeout=LAUNCH_TIMEOUT_SECONDS, launch=True)


def export_active_document(
    output_dir: Path,
    export_format: ExportFormat | str = ExportFormat.STEP,
    *,
    stem: str = "catia_export",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """Export whatever is active in CATIA and return the file written.

    The destination is built here from a caller-supplied directory and a
    sanitised stem -- a path from the model or the browser never reaches COM.
    """
    fmt = export_format if isinstance(export_format, ExportFormat) else ExportFormat.parse(
        str(export_format)
    )
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_stem = "".join(c for c in stem if c.isalnum() or c in "-_")[:64] or "catia_export"
    target = (output_dir / safe_stem).with_suffix(fmt.suffix)
    if not target.resolve().is_relative_to(output_dir):
        raise CATIAExportError("Refusing to export outside the target directory.")

    def work(catia: Any) -> Path:
        if int(catia.Documents.Count) == 0:
            raise CATIAExportError(
                "CATIA has no document open, so there is nothing to export. "
                "Open or create a part in CATIA first."
            )
        try:
            doc = catia.ActiveDocument
        except Exception as exc:
            raise CATIAExportError(
                "CATIA has documents open but none is active. Click the part "
                "window in CATIA, then try again."
            ) from exc

        try:
            # Push any pending feature edits into the geometry, or the export
            # can silently write the pre-edit shape.
            doc.Part.Update()
        except Exception:
            # Products and drawings have no .Part; updating is best-effort.
            logger.debug("CATIA document has no updatable Part", exc_info=True)

        try:
            doc.ExportData(str(target), fmt.value)
        except Exception as exc:
            raise CATIAExportError(
                f"CATIA refused to export as {fmt.value.upper()}: {exc}. "
                "This is usually a missing translator licence for that format."
            ) from exc
        return target

    result = _run_com(work, timeout=timeout, launch=False)

    if not result.exists() or result.stat().st_size == 0:
        raise CATIAExportError(
            f"CATIA reported success but wrote no usable {fmt.value.upper()} file. "
            "Check that the part contains solid geometry."
        )
    return result
