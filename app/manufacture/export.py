"""Neutral-format export: the solid itself, as a file somebody else can open.

`dxf.py` writes the *drawing*; this writes the *part*. They are different
artefacts for different readers — a machinist reads the drawing, a CAM programmer
and an inspection house want the solid — and a manufacturing package needs both.

**STEP, and specifically AP242.** Master plan 17.2 names it as "the one that
carries PMI": AP203 carries geometry, AP214 carries geometry plus colour and
assembly structure, and AP242 is the protocol that can carry product and
manufacturing information — tolerances, datums, annotation — alongside the
solid. Kryova does not write PMI yet (there is no GD&T in the design IR to write,
and `DimensionReport` says so in words on every drawing), so choosing AP242 today
buys the *file* being the right kind of file rather than a payload that is
already there. The schema is a parameter and the file states which one it is, for
the same reason the drawing states its projection convention: a receiving system
that guesses is a receiving system that guesses wrong once.

**The static parameters must be set *after* the writer is constructed, and this
is the gotcha that costs an afternoon.** `Interface_Static.SetCVal_s` returns
`False` — silently, with no exception and no effect — until something has
initialised the STEP resource set, and constructing `STEPControl_Writer` is what
does that. Set `write.step.schema` before the constructor and the call fails, the
return value is discarded by every example on the internet, and the file comes out
as AP214 with nothing anywhere saying so. `configure_writer` checks the return value and
refuses, so a future OCCT that renames a parameter is a refusal rather than a
wrong file.

**These bytes are not reproducible, and that is OCCT's decision rather than
ours.** A STEP file's `FILE_NAME` header carries the wall-clock time it was
written, so two exports of the same solid differ in their header. `dxf.py` can
scrub its four stamps because a DXF is a tag stream this codebase writes through
a library it controls; a STEP file is written by OCCT's own C++ writer in one
call. So the reproducibility claim here is about the *geometry*: the same shape
exported and re-read returns the same volume, extent and topology, and
`tests/test_manufacture_export.py` measures that rather than hashing the file.
Nothing in this module claims a file is verified because it is non-empty.

**Where the OCCT symbols ought to live.** `app/kernel/occt/binding.py` is the one
place OCCT may be imported, and `STEPControl_Writer`, `STEPControl_Reader`,
`Interface_Static` and `IFSelect_ReturnStatus` belong in its registry. They are
not in it today and this package does not own that file, so the import is
contained here in one guarded block with the same contract binding.py holds — the
module imports on a machine with no OCCT and refuses at call time. Move it when
the kernel's registry can take it; nothing outside `_step()` changes.

**What is deliberately not written here:** IGES, JT, 3MF and STL. 17.2 lists them
and OCCT can write all four, but an export nobody has verified round-trips is an
export that gets trusted, and each needs its own tolerance and its own honest
account of what it loses. STL in particular is a mesh — it throws the analytic
surfaces away — so writing one silently from a function called `export` would
hand a shop a faceted cylinder.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from app.manufacture.errors import ExportError


class StepSchema(StrEnum):
    """Which STEP application protocol a file is written to.

    The values are OCCT's own `write.step.schema` spellings, so nothing has to
    translate between what a caller asks for and what the writer is told — a
    translation table is one more place the two can disagree about what was
    written.
    """

    #: AP242 (managed model-based 3D engineering). The one that can carry PMI.
    AP242 = "AP242DIS"

    #: AP214 (automotive design). OCCT's default, and what most CAM seats
    #: expect. Named so a caller who needs it can ask rather than discover that
    #: the parameter was never applied.
    AP214 = "AP214IS"

    #: AP203 (configuration-controlled design). Geometry and nothing else; the
    #: safest thing to hand a very old system.
    AP203 = "AP203"


#: The unit STEP is told to write in. The codebase is mm-N-MPa and nothing
#: converts (CLAUDE.md), so this states that fact to the writer rather than
#: converting anything — and a STEP file whose length unit is inches while its
#: numbers are millimetres is the export equivalent of a DXF with no `$INSUNITS`.
STEP_UNIT: Final = "MM"

#: How far a re-read solid may differ from the one that was written, as a
#: fraction of its volume. STEP is an exact B-rep exchange — the surfaces are
#: written as surfaces, not tessellated — so the only loss is decimal text
#: precision in the file, which lands around 1e-9 relative. A tolerance loose
#: enough to pass a tessellated export would not be checking anything.
ROUND_TRIP_TOLERANCE: Final = 1e-6


@dataclass(frozen=True)
class StepExport:
    """What was written, said precisely enough to put in a manifest.

    `schema` is recorded because it is not inferable from the shape and is the
    thing a receiving system needs to know; `unit` because a number with no unit
    is not a measurement anywhere else in this codebase either.
    """

    path: Path
    schema: StepSchema
    unit: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "format": "STEP",
            "schema": str(self.schema),
            "unit": self.unit,
            "size_bytes": self.size_bytes,
        }


def _step() -> dict[str, Any]:
    """The OCCT STEP symbols, or a refusal that says how to get them.

    One guarded import, the same contract `app/kernel/occt/binding.py` holds:
    this package imports on a machine with no OCCT and refuses at the call that
    needed it, rather than failing at process start.
    """
    try:
        from OCP.IFSelect import IFSelect_ReturnStatus
        from OCP.Interface import Interface_Static
        from OCP.STEPControl import (
            STEPControl_Reader,
            STEPControl_StepModelType,
            STEPControl_Writer,
        )
    except ImportError as error:  # pragma: no cover - depends on the machine
        raise ExportError(
            "Writing STEP needs the OCCT kernel, which is not installed here. "
            "Install it with `pip install cadquery-ocp`, or export the part from "
            "the CATIA seat instead."
        ) from error
    return {
        "done": IFSelect_ReturnStatus.IFSelect_RetDone,
        "static": Interface_Static,
        "reader": STEPControl_Reader,
        "writer": STEPControl_Writer,
        "as_is": STEPControl_StepModelType.STEPControl_AsIs,
    }


def configure_writer(static: Any, schema: StepSchema) -> None:
    """Tell the writer its schema and its unit, and refuse if it did not listen.

    Public because `xde.py` writes through a *different* OCCT writer to the same
    static parameters, and two copies of this would be two places for the schema
    and the unit to disagree about what was asked for.

    `SetCVal_s` returns a bool and every example discards it. Checking it is the
    difference between "this file is AP242" and "this file is whatever OCCT
    defaults to, and the manifest says AP242".
    """
    for name, value in (
        ("write.step.schema", str(schema)),
        ("write.step.unit", STEP_UNIT),
    ):
        if not static.SetCVal_s(name, value):
            raise ExportError(
                f"OCCT refused to set {name} to {value!r}, so the file would be written "
                "to whatever the default is while the manifest claimed otherwise. This "
                "build's OCCT may not know that parameter name."
            )


def write_step(
    shape: Any, path: str | Path, *, schema: StepSchema = StepSchema.AP242
) -> StepExport:
    """Write one solid to a STEP file, returning what was written.

    Refuses an empty document rather than producing a file with no geometry in
    it: a 4 kB STEP holding a header and nothing else opens without error in
    every CAD system there is and shows an empty part, which is the export
    equivalent of a blank drawing.
    """
    if shape is None:
        raise ExportError(
            "There is nothing to export: this document holds no solid. Build a pad or "
            "a shaft first — a part whose last operation failed leaves a document that "
            "looks open and contains nothing."
        )
    target = Path(path)
    if target.suffix.lower() not in (".step", ".stp"):
        raise ExportError(
            f"A STEP file must be written to a .step or .stp path; got {target.name!r}. "
            "Every receiving system picks its reader from the extension."
        )
    target.parent.mkdir(parents=True, exist_ok=True)

    step = _step()
    writer = step["writer"]()
    configure_writer(step["static"], schema)

    status = writer.Transfer(shape, step["as_is"])
    if status != step["done"]:
        raise ExportError(
            f"OCCT could not translate this shape to STEP ({status}). Check the part is "
            "valid with the interrogation tools — a shape with an invalid face is "
            "usually what stops the transfer."
        )
    status = writer.Write(str(target))
    if status != step["done"]:
        raise ExportError(
            f"OCCT translated the shape but could not write {target} ({status}). Check "
            "the directory exists and is writable."
        )
    return StepExport(
        path=target,
        schema=schema,
        unit=STEP_UNIT.lower(),
        size_bytes=target.stat().st_size,
    )


def read_step(path: str | Path) -> Any:
    """Read a STEP file back as one shape.

    Exists so the round trip is checkable in a test rather than asserted. An
    export whose only evidence is that a file appeared is an export nobody has
    verified — the standing rule in this codebase is that an unmeasured claim is
    never a pass, and "the file is 19 kB" measures the wrong thing.
    """
    source = Path(path)
    if not source.is_file():
        raise ExportError(f"There is no STEP file at {source}.")
    step = _step()
    reader = step["reader"]()
    if reader.ReadFile(str(source)) != step["done"]:
        raise ExportError(
            f"{source.name} is not a STEP file OCCT can read. A file truncated in "
            "transit is the usual cause; check its size against the export record."
        )
    if reader.TransferRoots() == 0:
        raise ExportError(
            f"{source.name} parsed as STEP but carries no transferable shape. That is "
            "what a header-only file looks like."
        )
    return reader.OneShape()


def step_schema_of(path: str | Path) -> str:
    """The schema a STEP file declares, read out of its own header.

    Reading it back from the file rather than trusting what was requested is the
    only way to know the static parameter took effect, and the silent failure
    mode it guards against is the one `configure_writer`'s docstring describes.
    """
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ExportError(f"Could not read {source}: {error}") from error
    marker = "FILE_SCHEMA"
    start = text.find(marker)
    if start < 0:
        raise ExportError(
            f"{source.name} has no FILE_SCHEMA in its header, so it is not a STEP "
            "file this build wrote."
        )
    end = text.find(";", start)
    return text[start : end if end > start else start + 200]


__all__ = [
    "ROUND_TRIP_TOLERANCE",
    "STEP_UNIT",
    "StepExport",
    "StepSchema",
    "configure_writer",
    "read_step",
    "step_schema_of",
    "write_step",
]
