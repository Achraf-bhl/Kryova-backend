"""The metadata half of a STEP file: names, colours, layers, properties, PMI.

`export.py` writes the *shape* through `STEPControl_Writer`, which carries
geometry, topology and nothing else. Everything a receiving system asks for
besides the solid — what the part is called, what colour it is, what layer it is
on, what the file claims its own volume is, and what tolerance applies to it —
lives in OCCT's XDE layer: an `XCAFDoc` document written by
`STEPCAFControl_Writer`. This module is that path, and it exists so E21 task 1's
round-trip matrix can measure those classes instead of recording them as
untried.

**Nothing in the product exports through this yet**, and that is stated rather
than implied: `write_step` remains the shipped path. Wiring this in is E17's,
and the reason it has not happened is in `SEMANTIC_PMI_IS_WRONG_BY` below.

Four things measured on OCP 7.9.3.1 / OCCT 7.9.3 on 2026-09-09, each of which
cost a probe to find and none of which is inferable from the OCCT user guide:

1. **A tolerance attached to a label that is not in the shape tree is dropped,
   and the write still returns success.** `SetGeomTolerance` needs a label the
   `XCAFDoc_ShapeTool` knows: the part label, or a face registered with
   `AddSubShape`. `ShapeTool.FindShape(face, False)` on a face nobody added
   returns a **null label**, and `SetGeomTolerance` on it raises
   `Standard_NullObject` — so the obvious authoring route fails loudly. What
   fails *silently* is authoring a tolerance and binding it to nothing: the file
   writes, `RetDone` comes back, and there is no tolerance in it.

2. **The concrete STEP entity is `FLATNESS_TOLERANCE`, never the literal string
   `GEOMETRIC_TOLERANCE`.** AP242 writes the subtype. A probe grepping a written
   file for `GEOMETRIC_TOLERANCE` finds nothing *on success*, which is how the
   first pass at this concluded that OCCT writes no PMI at all.

3. **`SetPropsMode(True)` does not compute anything.** It enables the transfer of
   validation properties that are already on the document as `XCAFDoc_Volume`,
   `XCAFDoc_Area` and `XCAFDoc_Centroid` attributes. With the mode on and the
   attributes absent, the file gets no `PROPERTY_DEFINITION` at all and nothing
   says so. The mode is a permission, not a calculation.

4. **AP214 silently drops the tolerance.** The same document, the same modes, a
   successful write — and no tolerance entity in the file. So the schema decides
   whether PMI survives, and it decides it without telling anybody.

5. **The reader invents a name where none was written.** A part nobody named
   comes back as `'SOLID'` — the shape type standing in for an identity. So
   "something arrived" is not evidence that anything was carried, and every row
   in `interop.py` compares against what was *authored*.

**Two OCP binding landmines**, both of which end a process rather than raise:

- **`TDF_Label.FindAttribute(id, attr)` segfaults when the attribute is
  absent.** Not returns `False` — segfaults. Every read here goes through
  `_attribute`, which asks `IsAttribute(id)` first. This is the same class of
  hazard as the handle-by-value note in the kernel's CLAUDE.md section: the
  binding compiles, reads naturally, and takes the interpreter with it.
- **`XCAFDoc_LayerTool.GetLayers(label, seq)` returns `True` with an empty
  sequence** on a document read back from STEP. The assignment survives, but it
  is reachable only from the layer's side, through
  `GetShapesOfLayer_s(layerLabel, seq)`. Believing the first accessor records a
  class as `LOST` that is in fact carried, which is the worst kind of matrix
  entry: a defect reported against somebody else's code that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from app.manufacture.errors import ExportError
from app.manufacture.export import STEP_UNIT, StepExport, StepSchema, configure_writer

#: What a flatness tolerance authored as `0.05` comes back as when the same
#: OCCT build reads its own file: `50.0`. The writer emits the magnitude
#: unchanged under a `SI_UNIT($,.METRE.)` while the model's own length unit is
#: `SI_UNIT(.MILLI.,.METRE.)`, so the file states a tolerance one thousand times
#: the one that was authored, and the reader — correctly, given what the file
#: says — converts metres to millimetres on the way back.
#:
#: **This is not a convention this module misread.** A value written and read by
#: one build, through that build's own writer and reader, does not survive its
#: own round trip. That asymmetry is the evidence, and it is why
#: `semantic_pmi_survives_a_round_trip` re-measures rather than trusting this
#: comment.
#:
#: It is also exactly the failure the units rule in CLAUDE.md exists for — the
#: results page shipped a `/1000` once — so nothing here compensates for it.
#: Scaling on the way out would put a number in the file that no part of this
#: codebase believes, and would hide a defect a receiving system needs to know
#: about. The honest move is to measure it and refuse to claim the capability.
SEMANTIC_PMI_IS_WRONG_BY: Final = 1000.0

#: The XCAF document format. `BinXCAF` rather than `XmlOcaf` because nothing
#: here persists a document — it is written to STEP and dropped — and the binary
#: driver is what `XCAFApp_Application` hands out without further setup.
XCAF_FORMAT: Final = "BinXCAF"


@dataclass(frozen=True)
class Tolerance:
    """One geometric tolerance, in this codebase's units.

    `value_mm` is millimetres because everything here is millimetres. What OCCT
    does with it on the way into the file is `SEMANTIC_PMI_IS_WRONG_BY`'s
    problem, and it is reported rather than corrected.
    """

    value_mm: float
    kind: str = "flatness"


@dataclass(frozen=True)
class ValidationProperties:
    """What the file states about itself for the reader to check against.

    The most useful metadata class in a STEP file and the one most often
    missing: a receiving system that recomputes the volume and compares it with
    the number the file carries has caught a translation error without anybody
    opening the part.
    """

    volume_mm3: float
    area_mm2: float
    centroid_mm: tuple[float, float, float]


@dataclass(frozen=True)
class Occurrence:
    """One placement of the part inside an assembly."""

    name: str
    translation_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Annotations:
    """Everything besides the shape that this path can put in a file.

    Every field is optional and `None` means *not authored*, which the matrix
    reports as `NOT_ATTEMPTED` rather than as a loss — the same distinction the
    rest of `interop.py` is built on.
    """

    name: str | None = None
    colour_rgb: tuple[float, float, float] | None = None
    layer: str | None = None
    properties: ValidationProperties | None = None
    tolerance: Tolerance | None = None
    occurrences: tuple[Occurrence, ...] = ()


@dataclass(frozen=True)
class Recovered:
    """What came back out of a file, read through the same XDE layer.

    `colour_types` is recorded rather than collapsed into a bool because the
    type a colour comes back as is *not* the type it went in as — see
    `read_step_with_metadata`.
    """

    name: str | None = None
    colour_rgb: tuple[float, float, float] | None = None
    colour_types: tuple[str, ...] = ()
    layers: tuple[str, ...] = ()
    properties: ValidationProperties | None = None
    tolerance: Tolerance | None = None
    occurrences: tuple[str, ...] = ()
    entity_counts: dict[str, int] = field(default_factory=dict)

    #: The solid itself, so a caller measuring metadata can measure the geometry
    #: in the same trip rather than performing a second one and comparing two
    #: files that were never the same file.
    shape: Any | None = None


def _xde() -> dict[str, Any]:
    """The OCCT XDE symbols, or a refusal that says how to get them.

    One guarded import, the contract `app/kernel/occt/binding.py` holds and
    `export.py` already follows in this package: the module imports on a machine
    with no OCCT and refuses at the call that needed it.
    """
    try:
        from OCP.gp import gp_Pnt, gp_Trsf, gp_Vec
        from OCP.Interface import Interface_Static
        from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
        from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.TDataStd import TDataStd_Name
        from OCP.TDF import TDF_LabelSequence
        from OCP.TDocStd import TDocStd_Document
        from OCP.TopLoc import TopLoc_Location
        from OCP.XCAFApp import XCAFApp_Application
        from OCP.XCAFDimTolObjects import (
            XCAFDimTolObjects_GeomToleranceType_Flatness,
            XCAFDimTolObjects_GeomToleranceTypeValue_None,
        )
        from OCP.XCAFDoc import (
            XCAFDoc_Area,
            XCAFDoc_Centroid,
            XCAFDoc_ColorType,
            XCAFDoc_DocumentTool,
            XCAFDoc_GeomTolerance,
            XCAFDoc_LayerTool,
            XCAFDoc_ShapeTool,
            XCAFDoc_Volume,
        )
    except ImportError as error:  # pragma: no cover - depends on the machine
        raise ExportError(
            "Writing STEP metadata needs the OCCT kernel, which is not installed "
            "here. Install it with `pip install cadquery-ocp`."
        ) from error
    return {
        "Application": XCAFApp_Application,
        "Area": XCAFDoc_Area,
        "Centroid": XCAFDoc_Centroid,
        "ColorType": XCAFDoc_ColorType,
        "Colour": Quantity_Color,
        "Document": TDocStd_Document,
        "DocumentTool": XCAFDoc_DocumentTool,
        "Flatness": XCAFDimTolObjects_GeomToleranceType_Flatness,
        "GeomTolerance": XCAFDoc_GeomTolerance,
        "LabelSequence": TDF_LabelSequence,
        "LayerTool": XCAFDoc_LayerTool,
        "Location": TopLoc_Location,
        "Name": TDataStd_Name,
        "Point": gp_Pnt,
        "RGB": Quantity_TOC_RGB,
        "Reader": STEPCAFControl_Reader,
        "ShapeTool": XCAFDoc_ShapeTool,
        "Static": Interface_Static,
        "Text": TCollection_ExtendedString,
        "Transform": gp_Trsf,
        "Vector": gp_Vec,
        "Volume": XCAFDoc_Volume,
        "ValueUnspecified": XCAFDimTolObjects_GeomToleranceTypeValue_None,
        "Writer": STEPCAFControl_Writer,
    }


def _blank_document(xde: dict[str, Any]) -> Any:
    """An XCAF document, made the way `XCAFApp_Application` expects.

    A bare `TDocStd_Document` is enough to *write* from, and is what the first
    probe used. It is not enough to read into: the reader's transfer wants a
    document the application has registered, and the difference does not show up
    as an error on the write side, so both halves go through here.
    """
    application = xde["Application"].GetApplication_s()
    document = xde["Document"](xde["Text"](XCAF_FORMAT))
    application.NewDocument(xde["Text"](XCAF_FORMAT), document)
    return document


def _attribute(label: Any, attribute: Any) -> Any | None:
    """`FindAttribute`, without the segfault.

    `TDF_Label.FindAttribute(id, attr)` does not return `False` for an attribute
    that is not there — it takes the process down. `IsAttribute` answers the same
    question safely, so it is asked first, every time. Never call `FindAttribute`
    in this codebase without it.
    """
    identifier = type(attribute).GetID_s()
    if not label.IsAttribute(identifier):
        return None
    return attribute if label.FindAttribute(identifier, attribute) else None


def _author(xde: dict[str, Any], shape: Any, annotations: Annotations) -> Any:
    """Build the XCAF document that will be written."""
    document = _blank_document(xde)
    main = document.Main()
    shapes = xde["DocumentTool"].ShapeTool_s(main)
    colours = xde["DocumentTool"].ColorTool_s(main)
    layers = xde["DocumentTool"].LayerTool_s(main)
    dimtol = xde["DocumentTool"].DimTolTool_s(main)

    part = shapes.AddShape(shape, False)
    if annotations.name is not None:
        xde["Name"].Set_s(part, xde["Text"](annotations.name))
    if annotations.colour_rgb is not None:
        red, green, blue = annotations.colour_rgb
        colours.SetColor(
            part,
            xde["Colour"](red, green, blue, xde["RGB"]),
            xde["ColorType"].XCAFDoc_ColorGen,
        )
    if annotations.layer is not None:
        layers.SetLayer(part, xde["Text"](annotations.layer))
    if annotations.properties is not None:
        properties = annotations.properties
        xde["Volume"].Set_s(part, properties.volume_mm3)
        xde["Area"].Set_s(part, properties.area_mm2)
        xde["Centroid"].Set_s(part, xde["Point"](*properties.centroid_mm))
    if annotations.tolerance is not None:
        # Bound to the *part* label. A tolerance bound to a label the shape tool
        # does not know is dropped without complaint — see this module's
        # docstring, finding 1.
        tolerance_label = dimtol.AddGeomTolerance()
        attribute = xde["GeomTolerance"].Set_s(tolerance_label)
        obj = attribute.GetObject()
        obj.SetType(xde["Flatness"])
        obj.SetTypeOfValue(xde["ValueUnspecified"])
        obj.SetValue(annotations.tolerance.value_mm)
        attribute.SetObject(obj)
        dimtol.SetGeomTolerance(part, tolerance_label)

    if annotations.occurrences:
        assembly = shapes.NewShape()
        if annotations.name is not None:
            xde["Name"].Set_s(assembly, xde["Text"](f"{annotations.name}.assembly"))
        for occurrence in annotations.occurrences:
            transform = xde["Transform"]()
            transform.SetTranslation(xde["Vector"](*occurrence.translation_mm))
            placed = shapes.AddComponent(assembly, part, xde["Location"](transform))
            xde["Name"].Set_s(placed, xde["Text"](occurrence.name))
        shapes.UpdateAssemblies()
    return document


def write_step_with_metadata(
    shape: Any,
    path: str | Path,
    annotations: Annotations,
    *,
    schema: StepSchema = StepSchema.AP242,
) -> StepExport:
    """Write a shape and its metadata through OCCT's XDE layer.

    Refuses AP203 and AP214 when a tolerance was asked for, rather than writing
    a file that silently has none: `SetDimTolMode(True)` under AP214 succeeds,
    returns `RetDone`, and produces no tolerance entity. A caller who asked for
    a tolerance and got a clean return would have every reason to believe it is
    in there.
    """
    if shape is None:
        raise ExportError(
            "There is nothing to export: this document holds no solid. Build a pad "
            "or a shaft first."
        )
    target = Path(path)
    if target.suffix.lower() not in (".step", ".stp"):
        raise ExportError(
            f"A STEP file must be written to a .step or .stp path; got {target.name!r}. "
            "Every receiving system picks its reader from the extension."
        )
    if annotations.tolerance is not None and schema is not StepSchema.AP242:
        raise ExportError(
            f"A geometric tolerance cannot be written to {schema.name}: OCCT accepts "
            "the request, reports success and writes no tolerance entity. Ask for "
            "AP242, or drop the tolerance and say in the drawing that it is the "
            "carrier."
        )
    target.parent.mkdir(parents=True, exist_ok=True)

    xde = _xde()
    document = _author(xde, shape, annotations)
    writer = xde["Writer"]()
    # Every mode on: this path exists to measure what OCCT will carry, and a mode
    # left off would be recorded as OCCT declining to write something nobody
    # asked it to.
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    writer.SetLayerMode(True)
    writer.SetPropsMode(True)
    writer.SetDimTolMode(True)
    configure_writer(xde["Static"], schema)

    if not writer.Transfer(document):
        raise ExportError(
            "OCCT could not translate this document to STEP. Check the part is valid "
            "with the interrogation tools."
        )
    status = writer.Write(str(target))
    if str(status).split(".")[-1] != "IFSelect_RetDone":
        raise ExportError(
            f"OCCT translated the document but could not write {target} ({status}). "
            "Check the directory exists and is writable."
        )
    return StepExport(
        path=target,
        schema=schema,
        unit=STEP_UNIT.lower(),
        size_bytes=target.stat().st_size,
    )


def read_step_with_metadata(path: str | Path) -> Recovered:
    """Read a STEP file back through the XDE layer and report what is on it.

    Three accessor facts are baked in here because each of them produces a wrong
    answer rather than an error:

    - **A colour written as `ColorGen` comes back as `ColorSurf` and
      `ColorCurv`, never as `ColorGen`.** A caller that asks for the type it
      wrote gets `False` and would record the colour as lost. Every type is
      asked and the ones that answered are reported.
    - **The layer assignment is only reachable from the layer's side.**
      `GetLayers(shapeLabel, seq)` returns `True` with nothing in it; the
      shapes on a layer come from `GetShapesOfLayer_s`.
    - **Every attribute read is guarded by `IsAttribute`**, because
      `FindAttribute` on an absent attribute segfaults.
    """
    source = Path(path)
    if not source.is_file():
        raise ExportError(f"There is no STEP file at {source}.")

    xde = _xde()
    document = _blank_document(xde)
    reader = xde["Reader"]()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(True)
    reader.SetPropsMode(True)
    reader.SetGDTMode(True)
    if str(reader.ReadFile(str(source))).split(".")[-1] != "IFSelect_RetDone":
        raise ExportError(
            f"{source.name} is not a STEP file OCCT can read. A file truncated in "
            "transit is the usual cause."
        )
    if not reader.Transfer(document):
        raise ExportError(
            f"{source.name} parsed as STEP but carries no transferable shape. That is "
            "what a header-only file looks like."
        )

    main = document.Main()
    shapes = xde["DocumentTool"].ShapeTool_s(main)
    colours = xde["DocumentTool"].ColorTool_s(main)
    layers = xde["DocumentTool"].LayerTool_s(main)
    dimtol = xde["DocumentTool"].DimTolTool_s(main)

    free = xde["LabelSequence"]()
    shapes.GetFreeShapes(free)
    if not free.Length():
        return Recovered(entity_counts=_entity_counts(source))
    top = free.Value(1)

    # **The free shape of an assembly is the assembly, not the part**, and every
    # annotation on this path hangs off the part. Reading name, colour and
    # properties from `top` without descending returns `None` for all three on
    # exactly the documents where they were authored most carefully — the read
    # succeeds, the matrix records three losses, and none of them happened.
    occurrences: tuple[str, ...] = ()
    part = top
    if xde["ShapeTool"].IsAssembly_s(top):
        components = xde["LabelSequence"]()
        xde["ShapeTool"].GetComponents_s(top, components)
        names = []
        for index in range(components.Length()):
            component = components.Value(index + 1)
            attribute = _attribute(component, xde["Name"]())
            names.append(attribute.Get().ToExtString() if attribute is not None else "")
        occurrences = tuple(names)
        if components.Length():
            referred = type(top)()
            if xde["ShapeTool"].GetReferredShape_s(components.Value(1), referred):
                part = referred

    name_attribute = _attribute(part, xde["Name"]())
    name = name_attribute.Get().ToExtString() if name_attribute is not None else None

    shape = xde["ShapeTool"].GetShape_s(part)
    colour_rgb: tuple[float, float, float] | None = None
    found_types: list[str] = []
    for tag in ("Gen", "Surf", "Curv"):
        probe = xde["Colour"]()
        if colours.GetColor(shape, getattr(xde["ColorType"], f"XCAFDoc_Color{tag}"), probe):
            found_types.append(tag)
            colour_rgb = (probe.Red(), probe.Green(), probe.Blue())

    layer_labels = xde["LabelSequence"]()
    layers.GetLayerLabels(layer_labels)
    on_this_shape: list[str] = []
    for index in range(layer_labels.Length()):
        layer_label = layer_labels.Value(index + 1)
        assigned = xde["LabelSequence"]()
        xde["LayerTool"].GetShapesOfLayer_s(layer_label, assigned)
        if not assigned.Length():
            continue
        text = xde["Text"]()
        if layers.GetLayer(layer_label, text):
            on_this_shape.append(text.ToExtString())

    volume = _attribute(part, xde["Volume"]())
    area = _attribute(part, xde["Area"]())
    centroid = _attribute(part, xde["Centroid"]())
    properties = None
    if volume is not None and area is not None and centroid is not None:
        point = centroid.Get()
        properties = ValidationProperties(
            volume_mm3=volume.Get(),
            area_mm2=area.Get(),
            centroid_mm=(point.X(), point.Y(), point.Z()),
        )

    tolerances = xde["LabelSequence"]()
    dimtol.GetGeomToleranceLabels(tolerances)
    tolerance = None
    if tolerances.Length():
        obj = xde["GeomTolerance"].Set_s(tolerances.Value(1)).GetObject()
        tolerance = Tolerance(
            value_mm=obj.GetValue(),
            kind=str(obj.GetType()).split("_")[-1].lower(),
        )

    return Recovered(
        name=name,
        colour_rgb=colour_rgb,
        colour_types=tuple(found_types),
        layers=tuple(on_this_shape),
        properties=properties,
        tolerance=tolerance,
        occurrences=occurrences,
        entity_counts=_entity_counts(source),
        shape=shape,
    )


#: The STEP entity names each metadata class lands as. `FLATNESS_TOLERANCE` and
#: not `GEOMETRIC_TOLERANCE`: AP242 writes the concrete subtype, so a file that
#: carries a tolerance perfectly contains the supertype's name nowhere at all.
ENTITIES: Final[tuple[str, ...]] = (
    "FLATNESS_TOLERANCE",
    "GEOMETRIC_TOLERANCE",
    "PRESENTATION_LAYER_ASSIGNMENT",
    "COLOUR_RGB",
    "PROPERTY_DEFINITION",
    "VOLUME_MEASURE",
    "AREA_MEASURE",
    "NEXT_ASSEMBLY_USAGE_OCCURRENCE",
)


def _entity_counts(source: Path) -> dict[str, int]:
    """What is in the file as text, beside what the reader made of it.

    Both halves are needed and they answer different questions: the entity count
    says whether the *writer* put it there, the read-back says whether the
    *reader* can find it again. A class present in the text and absent from the
    read-back is a reader defect; absent from both is a writer defect. Recording
    only the second would report them identically.
    """
    text = source.read_text(encoding="utf-8", errors="replace")
    return {name: text.count(name) for name in ENTITIES}


def semantic_pmi_survives_a_round_trip(scratch: str | Path) -> tuple[bool, str]:
    """Ask this build whether a tolerance comes back as the number it went in as.

    Re-measured rather than recorded, for `accepted_schema_values`' reason: the
    day OCCT fixes this is the day the product may start claiming semantic PMI,
    and nobody is going to re-read a comment to notice. Returns the verdict and
    the measurement in words.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    authored = Tolerance(value_mm=0.05)
    target = Path(scratch) / "pmi-round-trip.step"
    write_step_with_metadata(
        BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(),
        target,
        Annotations(name="pmi-probe", tolerance=authored),
    )
    recovered = read_step_with_metadata(target).tolerance
    if recovered is None:
        return False, (
            "the tolerance was written and did not come back at all, which is a "
            "different finding from the ×1000 this measures"
        )
    intact = recovered.value_mm == authored.value_mm
    return intact, (
        f"authored {authored.value_mm} mm, read back {recovered.value_mm} mm "
        f"(×{recovered.value_mm / authored.value_mm:g}); the file writes the "
        "magnitude under SI_UNIT($,.METRE.) while the model is SI_UNIT(.MILLI.,.METRE.)"
    )


__all__ = [
    "ENTITIES",
    "SEMANTIC_PMI_IS_WRONG_BY",
    "XCAF_FORMAT",
    "Annotations",
    "Occurrence",
    "Recovered",
    "Tolerance",
    "ValidationProperties",
    "read_step_with_metadata",
    "semantic_pmi_survives_a_round_trip",
    "write_step_with_metadata",
]
