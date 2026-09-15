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

**Tessellated STEP is measured, not assumed, the same way the schema spellings
above are — master plan 21.3. The measurement came back partial, and it is
recorded as partial rather than rounded up.** `write.step.tessellated` and its
reader counterpart are real OCCT parameters: on this build (OCP 7.9.3.1)
`SetCVal_s`/`SetIVal_s` both take all three values (`0`/`Off`, `1`/`On`,
`2`/`OnNoBRep`) and the readback agrees, so the parameter name and spelling in
`TessellatedWrite` are not guessed.

**`On` attaches a tessellated representation beside the BRep — on an OCCT
primitive.** A bare `BRepPrimAPI_MakeBox`, a cylinder, a box with one filleted
edge and a box with a boolean-cut hole all wrote a `TESSELLATED_SOLID` entity
alongside `ADVANCED_FACE` when meshed with `BRepMesh_IncrementalMesh` first and
written with `On`. **The same setting, on this codebase's own multi-feature
`bracket()` design fixture (six bolt holes on a pattern, four corner fillets,
an edge break) — the shape every other test in this module and
`tests/test_manufacture_export_tessellated.py` actually exercises — writes no
`TESSELLATED_SOLID` at all**, with an identical entity count to `Off`, even
though `BRepMesh_IncrementalMesh` reports `IsDone()` and every one of its 34
faces carries a `Poly_Triangulation` on inspection. Curvature, a fillet, a
boolean cut and a finer deflection were each tried in isolation against a
primitive and none of them broke it alone, so the cause is not one of those
features by itself — it was not isolated further this session, and guessing
at one would be exactly the kind of assumption this note exists to replace.
**The consequence: `tessellated=ON` is proven only on a primitive shape.** A
caller exporting a real Kryova part gets a valid, unchanged BRep-only file —
`write_step`'s existing guarantees are untouched — but cannot yet rely on the
tessellated payload actually being there, and nothing here claims it is.
`TestTheTessellatedRepresentationIsAdditional` therefore pins **both**
findings: presence on a primitive, absence on the bracket fixture — so a
future OCCT release that starts attaching one on the bracket fails the test
that assumed it wouldn't, which is how the day this closes gets noticed
instead of assumed away.

`OnNoBRep` measured as a no-op on every shape tried, primitive or not: a file
written at `OnNoBRep` was byte-identical in entity shape to `Off`. The reading
that fits the evidence: `OnNoBRep` only suppresses BRep on a shape that
carries *none* — a face built from a bare `Poly_Triangulation` with no
underlying `Geom_Surface`, which is what a customer's mesh-only upload looks
like and is not a shape this codebase's own kernel ever produces or that this
session constructed to check.

**Wiring is a second, separate residual.** Even the primitive case is not
wired to P6's viewer or P4's attachment path — see master plan 21.3 — and
would not be worth wiring until the bracket-shaped gap above is closed, since
a real Kryova part is exactly what both callers would hand it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
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

class TessellatedWrite(StrEnum):
    """`write.step.tessellated` / `read.step.tessellated`, OCCT's own spellings
    and values, measured on this build (see the module docstring).

    `ON_NO_BREP` is included because it is a real, accepted OCCT value and a
    caller may one day construct a mesh-only shape this module never has — not
    because this module has verified what it does. Passing it to a Kryova solid
    measures as `OFF`.
    """

    #: BRep only. OCCT's own default; passing this changes nothing.
    OFF = "0"
    #: BRep, plus a tessellation written alongside it as an additional
    #: representation. The only value this module has measured doing something.
    ON = "1"
    #: Tessellation in place of BRep, *when the shape carries no BRep*.
    #: Unmeasured here — see the module docstring.
    ON_NO_BREP = "2"


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
    #: The ASN.1 object identifier the file's own `FILE_SCHEMA` declares, read
    #: back from the written file, or None when the header carries none.
    object_identifier: tuple[int, ...] | None = None
    #: Which AP242 edition that identifier names (`AP242_EDITIONS`), or None when
    #: the file is not AP242 *or* its identifier is one this table does not know.
    #: `object_identifier` beside it says which of the two.
    ap242_edition: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "format": "STEP",
            "schema": str(self.schema),
            "unit": self.unit,
            "size_bytes": self.size_bytes,
            "object_identifier": (
                None if self.object_identifier is None else list(self.object_identifier)
            ),
            "ap242_edition": self.ap242_edition,
        }


#: Where `AP242_EDITIONS` was read, and when (master plan E21.2).
AP242_EDITIONS_SOURCE: Final = "https://www.steptools.com/docs/stp_aim/notes_ap242e3.html"
AP242_EDITIONS_READ_ON: Final = "2026-09-15"

#: AP242's ASN.1 object identifiers, by edition, as STEP Tools lists them:
#: "{ 1 0 10303 442 1 1 4 } ASN/1 for first edition { 1 0 10303 442 3 1 4 } ASN/1
#: for second edition { 1 0 10303 442 4 1 4 } ASN/1 for third edition". The same
#: page says "The AP242 schema name has not changed between editions", which is
#: why the schema *name* in a header cannot say which edition a file is, and the
#: identifier is the only thing that can. The second edition's version arc is 3,
#: not 2 -- a table, not arithmetic. No identifier for ISO 10303-242:2025 was
#: read, so a file declaring one reads as edition None, not as a guess.
AP242_EDITIONS: Final[dict[tuple[int, ...], int]] = {
    (1, 0, 10303, 442, 1, 1, 4): 1,
    (1, 0, 10303, 442, 3, 1, 4): 2,
    (1, 0, 10303, 442, 4, 1, 4): 3,
}

#: The AP242 schema name every edition shares (read from a file this build wrote).
AP242_SCHEMA_NAME: Final = "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"

_OBJECT_IDENTIFIER = re.compile(r"\{([\d\s]+)\}")


def _step() -> dict[str, Any]:
    """The OCCT STEP symbols, or a refusal that says how to get them.

    One guarded import, the same contract `app/kernel/occt/binding.py` holds:
    this package imports on a machine with no OCCT and refuses at the call that
    needed it, rather than failing at process start.
    """
    try:
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
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
        "mesh": BRepMesh_IncrementalMesh,
        "copy": BRepBuilderAPI_Copy,
    }


def configure_writer(
    static: Any,
    schema: StepSchema,
    *,
    tessellated: TessellatedWrite = TessellatedWrite.OFF,
) -> None:
    """Tell the writer its schema, its unit and its tessellation mode, and
    refuse if it did not listen.

    Public because `xde.py` writes through a *different* OCCT writer to the same
    static parameters, and two copies of this would be two places for the schema
    and the unit to disagree about what was asked for.

    `SetCVal_s` returns a bool and every example discards it. Checking it is the
    difference between "this file is AP242" and "this file is whatever OCCT
    defaults to, and the manifest says AP242". `tessellated` defaults to `OFF` —
    OCCT's own default — so every existing caller is unaffected.
    """
    for name, value in (
        ("write.step.schema", str(schema)),
        ("write.step.unit", STEP_UNIT),
        ("write.step.tessellated", str(tessellated)),
    ):
        if not static.SetCVal_s(name, value):
            raise ExportError(
                f"OCCT refused to set {name} to {value!r}, so the file would be written "
                "to whatever the default is while the manifest claimed otherwise. This "
                "build's OCCT may not know that parameter name."
            )


def write_step(
    shape: Any,
    path: str | Path,
    *,
    schema: StepSchema = StepSchema.AP242,
    tessellated: TessellatedWrite = TessellatedWrite.OFF,
) -> StepExport:
    """Write one solid to a STEP file, returning what was written.

    Refuses an empty document rather than producing a file with no geometry in
    it: a 4 kB STEP holding a header and nothing else opens without error in
    every CAD system there is and shows an empty part, which is the export
    equivalent of a blank drawing.

    `tessellated=ON` adds a triangulated representation alongside the BRep —
    see the module docstring for what was measured and what was not.
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
    configure_writer(step["static"], schema, tessellated=tessellated)

    if tessellated is not TessellatedWrite.OFF:
        # The writer emits whatever triangulation is already attached to the
        # shape; a shape with none writes no TESSELLATED_SOLID entity even with
        # the parameter on. 0.1 mm matches this module's own round-trip
        # tolerance being a geometry claim, not a visual one — a viewer
        # consumer of this representation is P6's, and P6 has not asked for a
        # deflection yet, so this is a placeholder default rather than a tuned
        # one.
        #
        # **Meshed on a copy, never on the caller's shape.** `BRepMesh`
        # attaches the triangulation to the faces it is handed, in place, and
        # `BRepBndLib` then measures that triangulation — enlarged by its
        # deflection — instead of the exact surfaces. Meshing the caller's
        # shape turned a cached 120 mm bracket into a 120.207 mm one for every
        # drawing made after the export, in the same process, with nothing
        # raising. An export reads the part; it does not get to change it.
        shape = step["copy"](shape, True, False).Shape()
        step["mesh"](shape, 0.1)

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
    return with_declared_edition(
        StepExport(
            path=target,
            schema=schema,
            unit=STEP_UNIT.lower(),
            size_bytes=target.stat().st_size,
        )
    )


def read_step(
    path: str | Path, *, tessellated: TessellatedWrite = TessellatedWrite.ON
) -> Any:
    """Read a STEP file back as one shape.

    Exists so the round trip is checkable in a test rather than asserted. An
    export whose only evidence is that a file appeared is an export nobody has
    verified — the standing rule in this codebase is that an unmeasured claim is
    never a pass, and "the file is 19 kB" measures the wrong thing.

    `tessellated` defaults to `ON` here, the opposite of `write_step`'s `OFF` —
    a file this module wrote never carries a tessellation unless asked for, so
    reading is safe to leave permissive; a file from elsewhere may carry one
    Kryova never wrote, and refusing it by default would refuse files this
    reader is otherwise able to open. Set `read.step.tessellated` after
    constructing the reader, matching the writer's own gotcha (see the module
    docstring): the static resource set is what construction initialises.
    """
    source = Path(path)
    if not source.is_file():
        raise ExportError(f"There is no STEP file at {source}.")
    step = _step()
    reader = step["reader"]()
    if not step["static"].SetCVal_s("read.step.tessellated", str(tessellated)):
        raise ExportError(
            f"OCCT refused to set read.step.tessellated to {tessellated!r}. This "
            "build's OCCT may not know that parameter name."
        )
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


def object_identifier_of(file_schema: str) -> tuple[int, ...] | None:
    """The ASN.1 object identifier inside a `FILE_SCHEMA` string, or None.

    OCCT writes it with a trailing space (`{1 0 10303 442 1 1 4 }`) and STEP Tools
    prints it with spaces inside both braces, so it is compared as integers, never
    as text.
    """
    found = _OBJECT_IDENTIFIER.search(file_schema)
    if found is None:
        return None
    return tuple(int(arc) for arc in found.group(1).split())


def ap242_edition_of(file_schema: str) -> int | None:
    """Which AP242 edition a `FILE_SCHEMA` string declares.

    None for a file that is not AP242, for one with no identifier, and for an
    identifier `AP242_EDITIONS` does not hold. The last is deliberate: an edition
    this table was never told about is unknown, and naming the nearest one would
    be the edition-less "AP242" citation E21.2 exists to stop.
    """
    if AP242_SCHEMA_NAME not in file_schema.upper():
        return None
    identifier = object_identifier_of(file_schema)
    if identifier is None:
        return None
    return AP242_EDITIONS.get(identifier)


def with_declared_edition(record: StepExport) -> StepExport:
    """`record` with the identifier and edition its own file declares.

    Read from the file after it is written, for the reason `step_schema_of`
    gives: what was requested is not evidence of what was written.
    """
    declared = step_schema_of(record.path)
    return replace(
        record,
        object_identifier=object_identifier_of(declared),
        ap242_edition=ap242_edition_of(declared),
    )


__all__ = [
    "AP242_EDITIONS",
    "AP242_EDITIONS_READ_ON",
    "AP242_EDITIONS_SOURCE",
    "AP242_SCHEMA_NAME",
    "ROUND_TRIP_TOLERANCE",
    "STEP_UNIT",
    "StepExport",
    "StepSchema",
    "TessellatedWrite",
    "ap242_edition_of",
    "configure_writer",
    "object_identifier_of",
    "read_step",
    "step_schema_of",
    "with_declared_edition",
    "write_step",
]
