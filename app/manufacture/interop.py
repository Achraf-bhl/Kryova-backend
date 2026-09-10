"""What survives the trip out, measured on the way rather than declared.

`export.py` writes the file. This says **what is in it** — per class of thing a
receiving system might expect — and it answers by performing the round trip and
looking, never by reading the schema name off the header and inferring the rest.

**Why this module exists.** Master plan E17 task 2 calls AP242 "the one that
carries PMI", and the plan is right about the *protocol*. It was wrong about our
*build*. Three facts, measured on OCP 7.9.3.1 / OCCT 7.9.3 on 2026-09-09 and
re-measured by this module's tests on whatever build is present:

1. `write.step.schema` accepts `AP242DIS` and refuses `AP242IS` and `AP242`,
   while for AP214 it accepts the published-IS spelling `AP214IS`. So the only
   AP242 this build can write is the one OCCT names after the **Draft**
   International Standard, and no sentence anywhere in this product may claim an
   AP242 *IS edition* (Ed.1 2014, Ed.2 2020, Ed.3 2022, or ISO 10303-242:2025).
   `accepted_schema_values()` re-measures that rather than trusting this
   paragraph, because the day OCCT adds the spelling is the day this comment goes
   stale silently.
2. The header of a file written that way says three things that do not agree with
   each other: `FILE_SCHEMA` names
   `AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF {1 0 10303 442 1 1 4 }`,
   `APPLICATION_PROTOCOL_DEFINITION` says `'international standard'`, and the year
   beside it is `2013` — which is before AP242's first IS publication. This module
   does not adjudicate that; it records the strings, because the only body that
   can settle what the file *is* is the system reading it.
3. **The metadata classes are not in the file at all**, and that is a property of
   our export path rather than of STEP. `STEPControl_Writer` — what `export.py`
   uses — carries geometry, topology and assembly structure. Colours, names,
   layers, validation properties and GD&T live in OCCT's XDE layer
   (`STEPCAFControl_Writer` over an `XCAFDoc` document), which this codebase does
   not write. So those classes come back `NOT_ATTEMPTED`, naming what would carry
   them, and they are never `LOST` — "we tried and it did not survive" and "we
   never tried" are different findings, and only one of them is a bug report
   against OCCT.

**The rule this module exists to enforce: a class nobody measured is never a
pass.** It is the same rule `Coverage.by_measurement` enforces for requirements
and `flat.volume_mismatch_mm3` enforces for a folded blank. A capability matrix
whose green rows include things nobody checked is worse than no matrix, because
it is read as evidence.

**What a `CARRIED` verdict here does and does not license.** It says this OCCT
read back what this OCCT wrote. It says nothing about CATIA, NX, SolidWorks or a
CMM's importer, and a round trip through one implementation is the *weakest*
interoperability evidence there is — both ends share the same bugs. The
cross-implementation half needs the Windows seat and is written as owed rather
than implied.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from app.manufacture.errors import ExportError
from app.manufacture.export import (
    ROUND_TRIP_TOLERANCE,
    StepSchema,
    read_step,
    step_schema_of,
    write_step,
)
from app.manufacture.xde import (
    Annotations,
    Occurrence,
    Tolerance,
    ValidationProperties,
    read_step_with_metadata,
    write_step_with_metadata,
)


class EntityClass(StrEnum):
    """The classes of thing a receiving system asks a STEP file for.

    Deliberately the vocabulary of the *reader*, not of OCCT's API: a
    manufacturing engineer asks "did the tolerances come through", not "did the
    `XCAFDoc_DimTolTool` labels transfer".
    """

    GEOMETRY = "geometry"
    TOPOLOGY = "topology"
    ASSEMBLY_STRUCTURE = "assembly_structure"
    PART_NAMES = "part_names"
    COLOURS = "colours"
    LAYERS = "layers"
    VALIDATION_PROPERTIES = "validation_properties"
    SEMANTIC_PMI = "semantic_pmi"
    PRESENTATION_PMI = "presentation_pmi"
    SAVED_VIEWS = "saved_views"


class Carriage(StrEnum):
    """What happened to a class on the trip.

    `NOT_ATTEMPTED` is the load-bearing member. Without it every class this
    export path never writes would have to be recorded as `LOST`, which reads as
    a defect in STEP or in OCCT and is a defect in neither — it is scope.
    """

    #: Written, read back, and checked against what went in.
    CARRIED = "carried"
    #: Written by this path, and not there on the way back. A finding.
    LOST = "lost"
    #: Written, and back — as a **different value**. The worst of the three and
    #: the reason it is not folded into `LOST`: an absence is detectable by the
    #: receiving system on its own, and a wrong number is not. A tolerance that
    #: vanished gets queried; a tolerance that arrives a thousand times too loose
    #: gets manufactured to.
    CORRUPTED = "corrupted"
    #: This export path does not write it. `owner` says what would.
    NOT_ATTEMPTED = "not_attempted"


#: Per class this path does not write: what would carry it, and why this trip
#: could not measure it. Named so a reader of the matrix gets the next step
#: rather than only the bad news, and so the work is estimable without being
#: re-derived. The reasons differ, and flattening them into one sentence is how
#: "we do not author this" and "OCCT does not write this" become the same row.
CARRIER: Final[dict[EntityClass, tuple[str, str]]] = {
    EntityClass.ASSEMBLY_STRUCTURE: (
        "XCAFDoc_ShapeTool components via STEPCAFControl_Writer",
        "this path writes one shape. A compound crossing intact is not a product "
        "structure — it has no instances, no occurrence names and no where-used, "
        "which is the whole of what E14 means by an assembly",
    ),
    EntityClass.PART_NAMES: (
        "TDataStd_Name on XCAFDoc labels via STEPCAFControl_Writer",
        "STEPControl_Writer has nowhere to put a name: the shape it is handed "
        "carries geometry and topology and no identity",
    ),
    EntityClass.COLOURS: (
        "XCAFDoc_ColorTool via STEPCAFControl_Writer (SetColorMode)",
        "not written by this path; colour lives in OCCT's XDE layer",
    ),
    EntityClass.LAYERS: (
        "XCAFDoc_LayerTool via STEPCAFControl_Writer (SetLayerMode)",
        "not written by this path; layers live in OCCT's XDE layer",
    ),
    EntityClass.VALIDATION_PROPERTIES: (
        "STEPCAFControl_Writer SetPropsMode",
        "not written by this path — and this is the class most worth having, "
        "since validation properties are the file stating its own volume and "
        "centroid for the reader to check against",
    ),
    EntityClass.SEMANTIC_PMI: (
        "XCAFDoc_DimTolTool via STEPCAFControl_Writer (SetDimTolMode)",
        "not written by this path, and there is nothing upstream to write: the "
        "design IR holds no GD&T, which is why every drawing says so in words",
    ),
    EntityClass.PRESENTATION_PMI: (
        "XCAFDoc_DimTolTool presentation shapes, or a drawing (E17 task 1)",
        "not written by this path; today the drawing is the carrier of tolerance "
        "and the STEP file is the carrier of shape",
    ),
    EntityClass.SAVED_VIEWS: (
        "XCAFDoc_ViewTool via STEPCAFControl_Writer",
        "not written by this path; nothing upstream defines a saved view",
    ),
}

#: The AP242 spellings this build might plausibly be asked for. `AP242IS` is in
#: the list *because* it is expected to be refused: a build that starts accepting
#: it is a build where the IS claim becomes available, and the only way to notice
#: that is to keep asking.
AP242_SPELLINGS: Final[tuple[str, ...]] = ("AP242DIS", "AP242IS", "AP242")


@dataclass(frozen=True)
class Carried:
    """One row of the matrix.

    `evidence` is the measurement in words — "volume 6000.000000 mm³ both sides"
    — rather than a bare verdict, because a matrix that says only `carried` is a
    claim, and this codebase's standing rule is that a claim with nothing to open
    is not a status.
    """

    entity_class: EntityClass
    carriage: Carriage
    evidence: str
    owner: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": str(self.entity_class),
            "carriage": str(self.carriage),
            "evidence": self.evidence,
            "carried_by": self.owner,
        }


@dataclass(frozen=True)
class RoundTripMatrix:
    """What a Kryova → STEP → Kryova trip preserved, and under what schema.

    Frozen and self-describing so it can be published — this is the artefact E21
    owes the trust surface, and P10 task 3's rule is that a public capability
    claim names its evidence.
    """

    schema: StepSchema
    file_schema: str
    schema_values_accepted: dict[str, bool]
    rows: tuple[Carried, ...]

    #: Which OCCT writer produced the file. Two paths write STEP here and they
    #: carry different things, so a matrix that does not name its writer is a
    #: capability statement about an unnamed program — and the optimistic reading
    #: of two matrices side by side is the union of their green rows, which is
    #: true of neither.
    writer: str = "STEPControl_Writer"

    def row(self, entity_class: EntityClass) -> Carried:
        for row in self.rows:
            if row.entity_class is entity_class:
                return row
        raise KeyError(f"{entity_class} is not in this matrix")

    @property
    def carried(self) -> tuple[EntityClass, ...]:
        return tuple(r.entity_class for r in self.rows if r.carriage is Carriage.CARRIED)

    @property
    def corrupted(self) -> tuple[EntityClass, ...]:
        """Every class that came back as something other than what went in.

        Separate from `carried` and from `unmeasured` because it is the one a
        caller must not be able to miss: a reader scanning for absences finds
        nothing wrong here.
        """
        return tuple(r.entity_class for r in self.rows if r.carriage is Carriage.CORRUPTED)

    @property
    def unmeasured(self) -> tuple[EntityClass, ...]:
        """Every class this trip did not put evidence behind.

        Exists so a caller cannot read the matrix as "everything not listed as
        lost is fine". The honest reading of a matrix is its holes.
        """
        return tuple(
            r.entity_class for r in self.rows if r.carriage is Carriage.NOT_ATTEMPTED
        )

    @property
    def claims_an_is_edition(self) -> bool:
        """Whether this build could have written a published AP242 edition.

        False on every build measured so far, and it is what the product's words
        must be checked against: while this is false, "AP242" may be said only
        with "(the draft schema OCCT writes, not a published IS edition)" beside
        it.
        """
        return bool(self.schema_values_accepted.get("AP242IS"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "writer": self.writer,
            "schema_requested": str(self.schema),
            "file_schema": self.file_schema,
            "schema_values_accepted": dict(self.schema_values_accepted),
            "claims_an_is_edition": self.claims_an_is_edition,
            "rows": [r.to_dict() for r in self.rows],
            "corrupted": [str(c) for c in self.corrupted],
            "unmeasured": [str(c) for c in self.unmeasured],
        }


def accepted_schema_values(values: tuple[str, ...] = AP242_SPELLINGS) -> dict[str, bool]:
    """Ask OCCT which `write.step.schema` spellings it will take, and believe it.

    `Interface_Static.SetCVal_s` returns a bool that every example on the
    internet discards — `export.py`'s `_configure` is built on checking it. Here
    the return value is the *measurement*: it is how a build that has gained
    `AP242IS` announces itself without anybody re-reading a release note.

    The static parameter is left holding the last value that was accepted, which
    is why every caller of `write_step` sets it again immediately before writing.
    That is `_configure`'s job and this function does not do it for them.
    """
    try:
        from OCP.Interface import Interface_Static
        from OCP.STEPControl import STEPControl_Writer
    except ImportError as error:  # pragma: no cover - depends on the machine
        raise ExportError(
            "Measuring the STEP schema support needs the OCCT kernel, which is not "
            "installed here. Install it with `pip install cadquery-ocp`."
        ) from error

    # Constructing the writer is what initialises the STEP resource set; before
    # that every SetCVal_s returns False and the measurement would read as "this
    # build supports nothing at all".
    STEPControl_Writer()
    return {value: bool(Interface_Static.SetCVal_s("write.step.schema", value)) for value in values}


def _geometry_row(before: Any, after: Any) -> Carried:
    """Compare the solid that went out with the solid that came back.

    **A shape with no solid in it is `NOT_ATTEMPTED`, not `CARRIED`.** A wire or
    an open shell has no volume on either side, and `0.0 == 0.0` is the shape of
    every false pass in this codebase's history — it is the same failure as a
    zero gradient telling an optimiser it has arrived. `metrology.volume_mm3`
    already refuses rather than integrating an open shell, so the honest row is
    that refusal's own words, and the topology row still measures what it can.
    """
    from app.kernel.errors import MeasurementError
    from app.kernel.occt.metrology import bounding_box_mm, surface_area_mm2, volume_mm3

    try:
        v_before, v_after = volume_mm3(before), volume_mm3(after)
    except MeasurementError as refusal:
        return Carried(
            EntityClass.GEOMETRY,
            Carriage.NOT_ATTEMPTED,
            f"no volume to compare — {refusal}",
            owner="a shape that encloses a solid; the topology row still applies",
        )
    a_before, a_after = surface_area_mm2(before), surface_area_mm2(after)
    box_before, box_after = bounding_box_mm(before), bounding_box_mm(after)

    if v_before <= 0.0:  # pragma: no cover - the refusal above is what fires
        return Carried(
            EntityClass.GEOMETRY,
            Carriage.NOT_ATTEMPTED,
            f"the shape written measured {v_before} mm³, so a relative comparison "
            "would divide by nothing and any answer it gave would be arithmetic, "
            "not evidence",
            owner="a shape that encloses a solid",
        )
    drift = abs(v_after - v_before) / v_before
    same_box = all(
        abs(x - y) <= ROUND_TRIP_TOLERANCE * max(1.0, abs(x))
        for key in ("min", "max")
        for x, y in zip(box_before[key], box_after[key], strict=True)
    )
    carried = drift <= ROUND_TRIP_TOLERANCE and same_box
    return Carried(
        EntityClass.GEOMETRY,
        Carriage.CARRIED if carried else Carriage.LOST,
        f"volume {v_before:.6f} → {v_after:.6f} mm³ (relative drift {drift:.3e}, "
        f"tolerance {ROUND_TRIP_TOLERANCE:.0e}); area {a_before:.6f} → {a_after:.6f} mm²; "
        f"bounding box {'identical' if same_box else 'moved'}",
    )


def _topology_row(before: Any, after: Any) -> Carried:
    from app.kernel.occt.topology import census

    left, right = census(before), census(after)
    same = left == right
    return Carried(
        EntityClass.TOPOLOGY,
        Carriage.CARRIED if same else Carriage.LOST,
        f"census {left} → {right}" + ("" if same else " — the counts differ"),
    )


def measure_round_trip(
    shape: Any,
    *,
    schema: StepSchema = StepSchema.AP242,
    workdir: str | Path | None = None,
) -> RoundTripMatrix:
    """Write this shape, read it back, and report what came with it.

    The trip is performed for real: a file is written by `write_step` — the same
    function the product exports through, so this measures the shipped path and
    not a private one — read by `read_step`, and the two shapes compared. A
    matrix produced any other way would be a description of intentions.

    `workdir` exists for a caller that wants to keep the file (a verification
    report should); by default the trip happens in a temporary directory and
    leaves nothing behind.
    """
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(workdir or scratch) / "round-trip.step"
        write_step(shape, target, schema=schema)
        file_schema = step_schema_of(target)
        returned = read_step(target)

        rows = [_geometry_row(shape, returned), _topology_row(shape, returned)]
        rows += [
            Carried(entity_class, Carriage.NOT_ATTEMPTED, reason, owner=owner)
            for entity_class, (owner, reason) in CARRIER.items()
        ]
        return RoundTripMatrix(
            schema=schema,
            file_schema=file_schema,
            schema_values_accepted=accepted_schema_values(),
            rows=tuple(rows),
        )


#: What the XDE path still does not write, and what would.
METADATA_CARRIER: Final[dict[EntityClass, tuple[str, str]]] = {
    EntityClass.PRESENTATION_PMI: CARRIER[EntityClass.PRESENTATION_PMI],
    EntityClass.SAVED_VIEWS: CARRIER[EntityClass.SAVED_VIEWS],
}

#: How far a colour component may move across the trip. `Quantity_Color` holds
#: single-precision components, so `0.2` returns as `0.20000000298023224` — an
#: exact comparison records a perfectly carried colour as lost, and a tolerance
#: any looser than this would stop noticing a channel swap.
COLOUR_TOLERANCE: Final = 1e-6

#: The annotations the metadata trip authors. A fixed reference rather than a
#: caller's, because the matrix is a *capability statement* and two runs of it
#: must be comparable — a matrix measured on whatever the caller happened to
#: have is a matrix whose red rows might be the input's fault.
REFERENCE_ANNOTATIONS: Final = Annotations(
    name="Kryova-round-trip",
    colour_rgb=(0.2, 0.4, 0.9),
    layer="KRYOVA-INTEROP",
    properties=ValidationProperties(
        volume_mm3=6000.0, area_mm2=2200.0, centroid_mm=(5.0, 10.0, 15.0)
    ),
    tolerance=Tolerance(value_mm=0.05),
    occurrences=(Occurrence("instance.1"), Occurrence("instance.2", (100.0, 0.0, 0.0))),
)


def _named_row(
    entity_class: EntityClass, authored: Any, recovered: Any, *, note: str = ""
) -> Carried:
    """The plain case: one value out, one value back, compared for equality."""
    carried = authored == recovered
    return Carried(
        entity_class,
        Carriage.CARRIED if carried else Carriage.LOST,
        f"authored {authored!r}, read back {recovered!r}{note}",
    )


def _colour_row(authored: Annotations, recovered: Any) -> Carried:
    if recovered.colour_rgb is None:
        return Carried(
            EntityClass.COLOURS,
            Carriage.LOST,
            "a colour was authored and no colour of any type came back",
        )
    assert authored.colour_rgb is not None  # a colour row is built only when one was authored
    drift = max(abs(a - b) for a, b in zip(authored.colour_rgb, recovered.colour_rgb, strict=True))
    return Carried(
        EntityClass.COLOURS,
        Carriage.CARRIED if drift <= COLOUR_TOLERANCE else Carriage.CORRUPTED,
        f"authored {authored.colour_rgb} as ColorGen, read back "
        f"{tuple(round(c, 9) for c in recovered.colour_rgb)} as "
        f"{'+'.join(recovered.colour_types)} (max channel drift {drift:.3e}, "
        f"tolerance {COLOUR_TOLERANCE:.0e}). **The type is not the one that was "
        "written**: a caller asking XCAFDoc_ColorGen for it back is told there is "
        "no colour",
    )


def _properties_row(authored: Annotations, recovered: Any) -> Carried:
    if recovered.properties is None:
        return Carried(
            EntityClass.VALIDATION_PROPERTIES,
            Carriage.LOST,
            "volume, area and centroid were authored and none came back",
        )
    expected, got = authored.properties, recovered.properties
    assert expected is not None  # a properties row is built only when they were authored
    same = (
        abs(got.volume_mm3 - expected.volume_mm3) <= ROUND_TRIP_TOLERANCE * expected.volume_mm3
        and abs(got.area_mm2 - expected.area_mm2) <= ROUND_TRIP_TOLERANCE * expected.area_mm2
        and all(
            abs(a - b) <= ROUND_TRIP_TOLERANCE * max(1.0, abs(a))
            for a, b in zip(expected.centroid_mm, got.centroid_mm, strict=True)
        )
    )
    return Carried(
        EntityClass.VALIDATION_PROPERTIES,
        Carriage.CARRIED if same else Carriage.CORRUPTED,
        f"volume {expected.volume_mm3} → {got.volume_mm3} mm³, area "
        f"{expected.area_mm2} → {got.area_mm2} mm², centroid {expected.centroid_mm} → "
        f"{got.centroid_mm} mm. **SetPropsMode(True) writes none of this on its own** "
        "— the XCAFDoc_Volume/Area/Centroid attributes have to be on the document, "
        "and with the mode on and the attributes absent the file gets no "
        "PROPERTY_DEFINITION and nothing says so",
    )


def _semantic_pmi_row(authored: Annotations, recovered: Any) -> Carried:
    """The one row in this matrix that is neither carried nor lost."""
    expected = authored.tolerance
    assert expected is not None  # a PMI row is built only when a tolerance was authored
    got = recovered.tolerance
    if got is None:
        return Carried(
            EntityClass.SEMANTIC_PMI,
            Carriage.LOST,
            f"a {expected.kind} tolerance of {expected.value_mm} mm was written and "
            "no tolerance came back",
        )
    if got.value_mm == expected.value_mm and got.kind == expected.kind:
        return Carried(
            EntityClass.SEMANTIC_PMI,
            Carriage.CARRIED,
            f"{expected.kind} {expected.value_mm} mm survived intact — which this "
            "build did not do when it was measured on 2026-09-09, so read the ×1000 "
            "note in app/manufacture/xde.py and widen what the product claims",
        )
    return Carried(
        EntityClass.SEMANTIC_PMI,
        Carriage.CORRUPTED,
        f"the {got.kind} tolerance survived as an entity and its value did not: "
        f"authored {expected.value_mm} mm, read back {got.value_mm} mm "
        f"(×{got.value_mm / expected.value_mm:g}). The writer emits the magnitude "
        "unchanged under SI_UNIT($,.METRE.) while the model's own length unit is "
        "SI_UNIT(.MILLI.,.METRE.), so the file states a tolerance a thousand times "
        "the authored one and the reader converts it back faithfully. Nothing here "
        "compensates: a receiving system needs to know, and scaling on the way out "
        "would put a number in the file that no part of this codebase believes",
        owner="nobody yet — this is a defect in the trip, not a gap in the authoring",
    )


def measure_metadata_round_trip(
    shape: Any,
    *,
    schema: StepSchema = StepSchema.AP242,
    workdir: str | Path | None = None,
) -> RoundTripMatrix:
    """The same question as `measure_round_trip`, asked of the XDE path.

    `measure_round_trip` measures what the **shipped** export carries, and the
    honest answer there is geometry and topology. This measures what OCCT's XDE
    layer carries when everything is authored and every mode is on — which is
    what the product would get if `E17` wired `xde.write_step_with_metadata` in.
    They are two matrices because they are two programs, and `writer` on each
    says which.

    A caller must read `corrupted` as well as `unmeasured`: this trip has a row
    that comes back green to anyone scanning for absences and is wrong by a
    factor of a thousand.
    """
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(workdir or scratch) / "metadata-round-trip.step"
        authored = REFERENCE_ANNOTATIONS
        write_step_with_metadata(shape, target, authored, schema=schema)
        file_schema = step_schema_of(target)
        recovered = read_step_with_metadata(target)

        rows = [
            _geometry_row(shape, recovered.shape),
            _topology_row(shape, recovered.shape),
            _named_row(EntityClass.PART_NAMES, authored.name, recovered.name),
            _named_row(
                EntityClass.ASSEMBLY_STRUCTURE,
                tuple(o.name for o in authored.occurrences),
                recovered.occurrences,
                note=(
                    f"; the file carries {recovered.entity_counts.get('NEXT_ASSEMBLY_USAGE_OCCURRENCE', 0)}"
                    " NEXT_ASSEMBLY_USAGE_OCCURRENCE entities"
                ),
            ),
            _colour_row(authored, recovered),
            _named_row(
                EntityClass.LAYERS,
                (authored.layer,),
                recovered.layers,
                note=(
                    ". Reachable only from the layer's side: GetLayers(shapeLabel, seq) "
                    "returns True with an empty sequence, and the assignment comes from "
                    "GetShapesOfLayer_s"
                ),
            ),
            _properties_row(authored, recovered),
            _semantic_pmi_row(authored, recovered),
        ]
        rows += [
            Carried(entity_class, Carriage.NOT_ATTEMPTED, reason, owner=owner)
            for entity_class, (owner, reason) in METADATA_CARRIER.items()
        ]
        return RoundTripMatrix(
            schema=schema,
            file_schema=file_schema,
            schema_values_accepted=accepted_schema_values(),
            rows=tuple(rows),
            writer="STEPCAFControl_Writer",
        )


__all__ = [
    "AP242_SPELLINGS",
    "CARRIER",
    "COLOUR_TOLERANCE",
    "METADATA_CARRIER",
    "REFERENCE_ANNOTATIONS",
    "Carriage",
    "Carried",
    "EntityClass",
    "RoundTripMatrix",
    "accepted_schema_values",
    "measure_metadata_round_trip",
    "measure_round_trip",
]
