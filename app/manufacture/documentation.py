"""Technical documentation drafted from the product structure -- master plan E17 task 6.

Four documents the plan names, each derived from `app.assembly.structure.ProductStructure`,
which is the one description of what the machine is made of:

* **Parts catalogue**: item numbers over the bill of materials, with quantities, designs,
  materials and revisions, and the indented structure the items sit in.
* **Assembly sequence**: sub-assemblies before the assemblies that use them, each step naming
  what is fitted, in the order the author declared it.
* **Exploded view**: every occurrence moved away from its parent's origin by a factor the
  caller chooses, as placement data and, given each part's shape, as one OCCT compound drawn
  by `app.render` (hidden-line, byte-identical run to run). The factor is for legibility and
  moves nothing in the design.
* **Service manual draft**: the disassembly order (the assembly sequence reversed) and the
  service parts list. Maintenance intervals, lubricants, torques and hazards during service are
  the manufacturer's, and the draft lists them as not written.

**Instructions for use are a draft for a named person, never a finished document.** That is
`app/compliance/boundary.py`'s stance on the output, and it is enforced by construction: every
document here is a `Draft` with the person it was prepared for, it has no field that marks it
finished, and a draft with no named person is refused. Its digital delivery goes through
`app.compliance.instructions.unmet`, which `delivery_gaps` calls with the draft's own product
model, so a delivery plan naming another model is caught.

**What the manufacturer writes and this does not**: the information Annex III of Regulation
(EU) 2023/1230 requires in instructions for use. That annex is not quoted in this codebase yet,
so this module does not paraphrase its list; the draft says the manufacturer completes it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from app.assembly.structure import ProductStructure
from app.compliance.boundary import Stance
from app.compliance.instructions import DigitalDelivery, Unmet, unmet
from app.manufacture.errors import ManufactureError


class DocumentationError(ManufactureError):
    """A document that cannot be drafted as asked."""


class DocumentKind(StrEnum):
    PARTS_CATALOGUE = "parts catalogue"
    ASSEMBLY_INSTRUCTIONS = "assembly instructions"
    SERVICE_MANUAL = "service manual"
    INSTRUCTIONS_FOR_USE = "instructions for use"


@dataclass(frozen=True)
class CatalogueItem:
    item: int
    component: str
    quantity: int
    design: str = ""
    material: str = ""
    revision: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "component": self.component,
            "quantity": self.quantity,
            "design": self.design,
            "material": self.material,
            "revision": self.revision,
            "description": self.description,
        }


def parts_catalogue(structure: ProductStructure) -> tuple[CatalogueItem, ...]:
    """Every part the product uses, numbered from 1 in the bill of materials' order."""
    items = []
    for number, line in enumerate(structure.bill_of_materials(leaves_only=True), start=1):
        component = structure.component(line.component)
        items.append(
            CatalogueItem(
                item=number,
                component=line.component,
                quantity=line.quantity,
                design=line.design,
                material=line.material,
                revision=line.revision,
                description=component.description,
            )
        )
    return tuple(items)


@dataclass(frozen=True)
class Fit:
    component: str
    count: int
    instances: tuple[str, ...]


@dataclass(frozen=True)
class AssemblyStep:
    number: int
    assembly: str
    fits: tuple[Fit, ...]
    notes: tuple[str, ...] = ()

    def sentence(self) -> str:
        parts = ", ".join(f"{fit.count} x {fit.component}" for fit in self.fits)
        return f"Step {self.number}: assemble {self.assembly} from {parts}."

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "assembly": self.assembly,
            "sentence": self.sentence(),
            "fits": [
                {"component": f.component, "count": f.count, "instances": list(f.instances)}
                for f in self.fits
            ],
            "notes": list(self.notes),
        }


def assembly_sequence(structure: ProductStructure) -> tuple[AssemblyStep, ...]:
    """Each assembly once, after every sub-assembly it contains, children in declared order."""
    order: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        component = structure.component(name)
        for instance in component.instances:
            visit(instance.component)
        if not component.is_leaf:
            order.append(name)

    visit(structure.root)
    steps: list[AssemblyStep] = []
    for number, name in enumerate(order, start=1):
        component = structure.component(name)
        grouped: dict[str, list[str]] = {}
        for instance in component.instances:
            grouped.setdefault(instance.component, []).append(instance.segment)
        fits = tuple(Fit(child, len(segments), tuple(segments)) for child, segments in grouped.items())
        notes = tuple(
            f"{instance.segment}: {instance.note}" for instance in component.instances if instance.note
        )
        steps.append(AssemblyStep(number, name, fits, notes))
    return tuple(steps)


@dataclass(frozen=True)
class ExplodedPlacement:
    path: str
    component: str
    origin_mm: tuple[float, float, float]
    exploded_origin_mm: tuple[float, float, float]
    #: The occurrence's world rotation, row-major; an explosion moves and never turns.
    rotation: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def exploded_view(structure: ProductStructure, *, factor: float) -> tuple[ExplodedPlacement, ...]:
    """Each occurrence pushed away from its parent's origin by `factor` times its offset.

    Applied level by level, so a sub-assembly moves as a whole and its parts spread within it.
    An occurrence at its parent's origin does not move; stacking such parts apart is a
    drawing decision, left to whoever lays the view out.
    """
    if not factor >= 0.0:
        raise DocumentationError(
            f"An explosion factor of {factor} would pull parts through each other. Give 0 or more."
        )
    occurrences = list(structure.occurrences(leaves_only=False))
    by_path = {occ.path: occ for occ in occurrences}
    exploded: dict[str, tuple[float, float, float]] = {}
    out: list[ExplodedPlacement] = []
    for occ in occurrences:  # depth-first, so a parent is always placed before its children
        origin = tuple(float(c) for c in occ.frame.origin_mm)
        parent_path = occ.parent_path
        if parent_path is None:
            moved = origin
        else:
            parent = by_path[parent_path]
            parent_origin = tuple(float(c) for c in parent.frame.origin_mm)
            parent_moved = exploded[parent_path]
            moved = tuple(
                parent_moved[k] + (origin[k] - parent_origin[k]) * (1.0 + factor) for k in range(3)
            )
        exploded[occ.path] = moved  # type: ignore[assignment]
        if structure.component(occ.component).is_leaf:
            out.append(
                ExplodedPlacement(
                    occ.path, occ.component, origin, moved, tuple(occ.frame.rotation)  # type: ignore[arg-type]
                )
            )
    return tuple(out)


def exploded_shape(
    structure: ProductStructure, shapes: Mapping[str, Any], *, factor: float
) -> Any:
    """Every part's shape placed at its exploded position, as one OCCT compound.

    `shapes` maps a component name to its shape in the component's own coordinates, the
    same contract `app.assembly.clash` uses. A part with no shape is refused by name rather
    than left out of the picture, because a view missing a part reads as a machine without it.
    """
    from app.kernel.occt.binding import symbol

    placements = exploded_view(structure, factor=factor)
    missing = sorted({p.component for p in placements if p.component not in shapes})
    if missing:
        raise DocumentationError(
            f"No shape was given for {', '.join(missing)}, so the exploded view would leave "
            "it out. Pass a shape for every part."
        )
    builder = symbol("BRep_Builder")()
    compound = symbol("TopoDS_Compound")()
    builder.MakeCompound(compound)
    for placement in placements:
        r, o = placement.rotation, placement.exploded_origin_mm
        transform = symbol("gp_Trsf")()
        transform.SetValues(
            r[0], r[1], r[2], o[0],
            r[3], r[4], r[5], o[1],
            r[6], r[7], r[8], o[2],
        )
        moved = symbol("BRepBuilderAPI_Transform")(shapes[placement.component], transform, True).Shape()
        builder.Add(compound, moved)
    return compound


def render_exploded(
    structure: ProductStructure,
    shapes: Mapping[str, Any],
    *,
    factor: float,
    view: str = "iso",
) -> Any:
    """The exploded view as a deterministic hidden-line picture (`app.render.render`)."""
    from app.render import render

    return render(exploded_shape(structure, shapes, factor=factor), view)


@dataclass(frozen=True)
class Draft:
    """A document prepared for a named person to complete and take as their own."""

    kind: DocumentKind
    product_model: str
    prepared_for: str
    sections: dict[str, Any]
    not_written: tuple[str, ...]
    stance: Stance = field(default=Stance.DRAFT_FOR_A_NAMED_PERSON, init=False)

    def __post_init__(self) -> None:
        if not self.prepared_for.strip():
            raise DocumentationError(
                f"A {self.kind.value} draft is prepared for a named person who completes it and "
                "takes it as their own. Name them; a draft for nobody reads as a finished document."
            )
        if not self.product_model.strip():
            raise DocumentationError(
                f"A {self.kind.value} must clearly describe the product model it belongs to. "
                "Give the model."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "status": "draft",
            "stance": self.stance.value,
            "product_model": self.product_model,
            "prepared_for": self.prepared_for,
            "sections": self.sections,
            "not_written": list(self.not_written),
        }


def _catalogue_section(structure: ProductStructure) -> list[dict[str, Any]]:
    return [item.to_dict() for item in parts_catalogue(structure)]


def draft_parts_catalogue(
    structure: ProductStructure, *, product_model: str, prepared_for: str
) -> Draft:
    return Draft(
        kind=DocumentKind.PARTS_CATALOGUE,
        product_model=product_model,
        prepared_for=prepared_for,
        sections={"items": _catalogue_section(structure)},
        not_written=("supplier part numbers and prices", "which items are sold as spares"),
    )


def draft_assembly_instructions(
    structure: ProductStructure, *, product_model: str, prepared_for: str, explode_factor: float = 1.0
) -> Draft:
    return Draft(
        kind=DocumentKind.ASSEMBLY_INSTRUCTIONS,
        product_model=product_model,
        prepared_for=prepared_for,
        sections={
            "parts": _catalogue_section(structure),
            "steps": [step.to_dict() for step in assembly_sequence(structure)],
            "exploded_view": [
                {
                    "path": p.path,
                    "component": p.component,
                    "origin_mm": list(p.origin_mm),
                    "exploded_origin_mm": list(p.exploded_origin_mm),
                }
                for p in exploded_view(structure, factor=explode_factor)
            ],
        },
        not_written=(
            "tightening torques and sequences",
            "tools, lifting and handling",
            "checks after each step",
            "hazards during assembly",
        ),
    )


def draft_service_manual(
    structure: ProductStructure, *, product_model: str, prepared_for: str
) -> Draft:
    steps = assembly_sequence(structure)
    return Draft(
        kind=DocumentKind.SERVICE_MANUAL,
        product_model=product_model,
        prepared_for=prepared_for,
        sections={
            "disassembly": [
                {"number": n, "assembly": step.assembly, "remove": [f.component for f in reversed(step.fits)]}
                for n, step in enumerate(reversed(steps), start=1)
            ],
            "service_parts": _catalogue_section(structure),
        },
        not_written=(
            "maintenance intervals",
            "lubricants and consumables",
            "tightening torques on reassembly",
            "hazards during service and how to make the machine safe first",
        ),
    )


def draft_instructions_for_use(
    structure: ProductStructure, *, product_model: str, prepared_for: str
) -> Draft:
    return Draft(
        kind=DocumentKind.INSTRUCTIONS_FOR_USE,
        product_model=product_model,
        prepared_for=prepared_for,
        sections={
            "identification": {"product_model": product_model},
            "parts": _catalogue_section(structure),
        },
        not_written=(
            "the information Annex III of Regulation (EU) 2023/1230 requires, which the "
            "manufacturer writes; the annex is not quoted in this codebase, so it is not listed here",
            "intended use and reasonably foreseeable misuse",
            "residual risks",
        ),
    )


def delivery_gaps(draft: Draft, delivery: DigitalDelivery) -> tuple[Unmet, ...]:
    """Article 10(7) conditions the delivery plan does not meet, checked with the draft's
    own product model as the model the instructions name. Empty means none this checker can
    see, never that the instructions are adequate."""
    if draft.kind is not DocumentKind.INSTRUCTIONS_FOR_USE:
        raise DocumentationError(
            f"Article 10(7) is about instructions for use; this is a {draft.kind.value}."
        )
    return unmet(replace(delivery, model_named_in_instructions=draft.product_model))


__all__ = [
    "AssemblyStep",
    "CatalogueItem",
    "DocumentKind",
    "DocumentationError",
    "Draft",
    "ExplodedPlacement",
    "Fit",
    "assembly_sequence",
    "delivery_gaps",
    "draft_assembly_instructions",
    "draft_instructions_for_use",
    "draft_parts_catalogue",
    "draft_service_manual",
    "exploded_shape",
    "exploded_view",
    "parts_catalogue",
    "render_exploded",
]
