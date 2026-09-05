"""The declarative vocabulary every CATIA operation is written in.

An operation is **data**, not code: a name, a tier, prompt text, and a list of
typed parameters. Four separate consumers read that data and none of them may
disagree with the others —

* the agent's tool schema (`app.catia.tool_specs`),
* the daemon's independent re-validation table (generated, see
  `scripts/gen_bridge_tools.py`),
* the backend method the daemon dispatches to,
* the docs and licence gating that key off `workbench`.

Before this module those four were four hand-written copies of the same facts,
which is why the tool count stopped at 39: adding one operation meant editing
four files in step and the fourth was always the one that got missed.

**Parameters are semantic.** `length("Depth of the pocket")` is not merely
`{"type": "number"}` — it carries the millimetre ceiling from `limits`, the
"greater than zero" that stops a zero-depth pocket, and the unit in its own
description so the model never has to infer it. Reach for the constructor that
names what the value *is*; only fall through to `raw()` when nothing fits, and
add a constructor when you find yourself doing that twice.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.catia.ops import limits


class Tier(StrEnum):
    """What an operation is allowed to do, and therefore what it must clear.

    Mirrors `tool_specs.CatiaTier`; kept as its own enum so the registry has no
    import edge back into the module that consumes it.
    """

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class Workbench(StrEnum):
    """The CATIA workbench an operation belongs to.

    Two uses, both real: the licence check (`app.catia_kb.licensing` knows which
    seats can open which workbench, and refusing early beats a COM error the
    user cannot read), and grouping in generated documentation.
    """

    INFRASTRUCTURE = "Infrastructure"
    SKETCHER = "Sketcher"
    PART_DESIGN = "Part Design"
    GENERATIVE_SHAPE_DESIGN = "Generative Shape Design"
    ASSEMBLY_DESIGN = "Assembly Design"
    DRAFTING = "Drafting"
    KNOWLEDGE_ADVISOR = "Knowledge Advisor"
    SHEET_METAL = "Sheet Metal Design"
    ANALYSIS = "Generative Structural Analysis"
    DMU = "DMU Navigator"


@dataclass(frozen=True)
class Param:
    """One tool argument: its JSON Schema plus whether it must be supplied."""

    name: str
    schema: dict[str, Any]
    required: bool = False

    #: The server fills this in after validating the model's arguments, and the
    #: model must not be able to supply it. It is therefore absent from the
    #: model-facing schema and present — required — in the daemon's.
    #:
    #: `catia_set_material.density_kg_m3` is the case this exists for. Every
    #: reported mass is computed from that density, so letting the model choose
    #: it would let a confused agent quietly change what the part weighs. The
    #: asymmetry is load-bearing and was once a live bug in the other direction:
    #: adding the field *before* validation made the model-facing schema reject
    #: the server's own key, and every call fell back to CATIA's default
    #: 1000 kg/m³ while the unit tests — which drove the daemon's schema —
    #: passed.
    supplied_by_server: bool = False

    #: The model supplies this and the server *consumes* it, resolving it into
    #: one of the operation's `server_fields` rather than forwarding it. It is
    #: therefore present — required — in the model-facing schema and absent from
    #: the daemon's, which never sees it.
    #:
    #: `catia_restore.checkpoint_id` and `.approval_token` are the case: the
    #: server looks the checkpoint up, verifies it belongs to this
    #: conversation's document, and sends `{"checkpoint": {...}}` alone. A
    #: daemon schema that required the original two would refuse every restore.
    consumed_by_server: bool = False

    def __post_init__(self) -> None:
        if self.supplied_by_server and self.consumed_by_server:
            raise ValueError(
                f"{self.name!r} cannot be both supplied and consumed by the server: "
                "the first means the model may not send it, the second means it must."
            )
        if not self.name.isidentifier():
            raise ValueError(f"parameter name {self.name!r} is not a valid identifier")
        if "description" not in self.schema:
            raise ValueError(
                f"parameter {self.name!r} has no description. The model reads these to "
                "choose values; an undescribed parameter is one it will guess at."
            )


@dataclass(frozen=True)
class Operation:
    """One callable CATIA operation, declared once and consumed four ways."""

    name: str
    summary: str
    tier: Tier
    workbench: Workbench
    params: Sequence[Param] = field(default_factory=tuple)

    #: Keys the *server* adds that the model never supplies — a resolved
    #: document path, a checkpoint blob, an inline-transfer ceiling. Enumerated
    #: per operation so "the server may add fields" never widens into "any field
    #: is accepted".
    server_fields: tuple[str, ...] = ()

    #: Backend method name. Defaults to the tool name minus its `catia_` prefix,
    #: which is the convention every existing tool already follows.
    method: str = ""

    #: Uses the export timeout rather than the call timeout. A STEP write or a
    #: full-part re-tessellation legitimately takes minutes.
    long_running: bool = False

    #: Set when the operation cannot be undone by restoring a checkpoint, so
    #: the auto-checkpoint before it would be a false reassurance.
    no_auto_checkpoint: bool = False

    #: Answered by the server without ever reaching a workstation. It has no
    #: backend method, and a frame carrying it is a fault upstream rather than
    #: a call to run — the daemon refuses it rather than guessing.
    server_only: bool = False

    def __post_init__(self) -> None:
        if not self.name.startswith("catia_"):
            raise ValueError(f"{self.name!r} must start with 'catia_'")
        if self.server_only:
            if self.method:
                raise ValueError(
                    f"{self.name}: a server-only operation must not name a backend "
                    "method — there is no device call behind it."
                )
        elif not self.method:
            object.__setattr__(self, "method", self.name.removeprefix("catia_"))
        seen: set[str] = set()
        for param in self.params:
            if param.name in seen:
                raise ValueError(f"{self.name}: duplicate parameter {param.name!r}")
            seen.add(param.name)
        overlap = seen & set(self.server_fields)
        if overlap:
            raise ValueError(
                f"{self.name}: {sorted(overlap)} declared as both a model parameter and a "
                "server field. A field the server fills must not also be model-writable."
            )

    @property
    def mutating(self) -> bool:
        return self.tier is not Tier.READ

    def _schema_over(self, params: Sequence[Param]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {param.name: param.schema for param in params},
            "required": [param.name for param in params if param.required],
            "additionalProperties": False,
        }

    def json_schema(self) -> dict[str, Any]:
        """The JSON Schema object the *model* is validated against.

        Excludes server-supplied parameters, so a model that tries to set one
        is refused by `additionalProperties: false` rather than obeyed.

        That strictness is not decoration. An unknown field means the model has
        misunderstood the tool, and accepting it means the daemon silently drops
        whatever the model actually meant.
        """
        return self._schema_over([p for p in self.params if not p.supplied_by_server])

    def daemon_schema(self) -> dict[str, Any]:
        """The JSON Schema the *daemon* re-validates the arrived call against.

        Includes server-supplied parameters, because by the time the frame
        reaches the daemon the server has filled them in and their absence is
        itself a fault worth refusing. Excludes server-*consumed* ones, which
        the server resolves into a `server_fields` key and never forwards.
        """
        return self._schema_over([p for p in self.params if not p.consumed_by_server])

    @property
    def server_supplied_fields(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params if p.supplied_by_server)


# -- parameter constructors -------------------------------------------------
#
# Each one names a *kind* of quantity and carries that kind's bound and unit.
# The description argument is prose for the model; the unit is appended here so
# no call site can forget it or contradict it.


#: How many per-entity values one argument may carry. Matches `catia_fillet_edges`'
#: `maxItems`, since both express the same thing: a hand-authored list of edge sizes.
MAX_PER_ENTITY_VALUES: Final = 50


def length(description: str, *, maximum: float = limits.MAX_LENGTH_MM) -> dict[str, Any]:
    """A strictly positive distance in millimetres."""
    return {
        "type": "number",
        "exclusiveMinimum": 0,
        "maximum": maximum,
        "description": f"{description} Millimetres.",
    }


def feature_length(description: str) -> dict[str, Any]:
    """A local dimension — radius, fillet, chamfer — on a tighter bound."""
    return length(description, maximum=limits.MAX_FEATURE_MM)


def feature_length_per_entity(description: str) -> dict[str, Any]:
    """A local dimension that may be given once, or once per selected entity.

    Master plan 2.3 — per-entity parameters — is what makes "the four vertical edges at
    2, 3, 4 and 5 mm" one call against a *predicate*, rather than four calls against four
    edge ids that stop meaning anything the moment an upstream feature is inserted.

    The backend has taken a list here since 2.3 landed (`_sizes_for` in the OCCT dress-up
    module matches it against the resolution order `resolve()` guarantees, and refuses a
    length mismatch rather than padding it). This declaration did not, which made the
    capability unreachable from the design IR — that layer validates against the registry
    before it compiles anything, so a spec asking for it was refused with "must be
    number". Found by E2's Proof, which is the first thing to author a part through the
    IR and the kernel together.
    """
    single = feature_length(description)
    return {
        **single,
        "type": ["number", "array"],
        "items": feature_length(f"{description} This entity's own value."),
        "minItems": 1,
        "maxItems": MAX_PER_ENTITY_VALUES,
        "description": (
            f"{description} Millimetres. One number applies to every selected entity; a "
            "list gives one per entity, in selection order, and must be exactly as long "
            "as the selection."
        ),
    }


def thickness(description: str) -> dict[str, Any]:
    """A wall, shell, sheet or ply thickness."""
    return length(description, maximum=limits.MAX_THICKNESS_MM)


def coordinate(description: str) -> dict[str, Any]:
    """A signed position along one axis, in millimetres."""
    return {
        "type": "number",
        "minimum": limits.MIN_COORD_MM,
        "maximum": limits.MAX_COORD_MM,
        "description": f"{description} Millimetres, signed, in the part's own frame.",
    }


def distance(description: str) -> dict[str, Any]:
    """A signed offset — may be negative to mean 'the other side'."""
    return {
        "type": "number",
        "minimum": limits.MIN_COORD_MM,
        "maximum": limits.MAX_COORD_MM,
        "description": f"{description} Millimetres; negative reverses the direction.",
    }


def angle(description: str, *, maximum: float = limits.MAX_ANGLE_DEG) -> dict[str, Any]:
    """A rotation or sweep in degrees."""
    return {
        "type": "number",
        "exclusiveMinimum": 0,
        "maximum": maximum,
        "description": f"{description} Degrees.",
    }


def signed_angle(description: str) -> dict[str, Any]:
    """An angle that may be negative, e.g. a rotation direction."""
    return {
        "type": "number",
        "minimum": -limits.MAX_ANGLE_DEG,
        "maximum": limits.MAX_ANGLE_DEG,
        "description": f"{description} Degrees; negative reverses the direction.",
    }


def tilt(description: str) -> dict[str, Any]:
    """A draft or chamfer angle, bounded short of degenerate."""
    return angle(description, maximum=limits.MAX_TILT_DEG)


def ratio(description: str) -> dict[str, Any]:
    """A scale or affinity factor. 1.0 is identity."""
    return {
        "type": "number",
        "minimum": limits.MIN_RATIO,
        "maximum": limits.MAX_RATIO,
        "description": f"{description} A ratio, where 1.0 leaves the size unchanged.",
    }


def count(description: str, *, minimum: int = 1, maximum: int = limits.MAX_INSTANCES) -> dict[str, Any]:
    """A whole number of instances, sides or repeats."""
    return {
        "type": "integer",
        "minimum": minimum,
        "maximum": maximum,
        "description": description,
    }


def flag(description: str) -> dict[str, Any]:
    """A boolean switch. Always describe what *false* means as well as true."""
    return {"type": "boolean", "description": description}


def name_of(description: str) -> dict[str, Any]:
    """The name of an existing feature, sketch, body or element."""
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": limits.MAX_NAME_CHARS,
        "description": description,
    }


def new_name(description: str) -> dict[str, Any]:
    """A name the operation will create."""
    return name_of(description)


def text(description: str, *, maximum: int = limits.MAX_TEXT_CHARS) -> dict[str, Any]:
    """Free text — an annotation, a note, a label."""
    return {"type": "string", "maxLength": maximum, "description": description}


def one_of(options: Iterable[str], description: str) -> dict[str, Any]:
    """A closed set of string choices.

    Closed on purpose: an open string is a value the daemon has to guess at,
    and a guess in CAD is a wrong part rather than an error message.
    """
    return {"type": "string", "enum": list(options), "description": description}


def point3(description: str) -> dict[str, Any]:
    """An (x, y, z) position in the part's frame, in millimetres.

    This is the parameter that removes the single largest limit in the old tool
    set — that every sketch primitive was centred on the origin because no tool
    could carry a coordinate.
    """
    return {
        "type": "array",
        "minItems": 3,
        "maxItems": 3,
        "items": {
            "type": "number",
            "minimum": limits.MIN_COORD_MM,
            "maximum": limits.MAX_COORD_MM,
        },
        "description": f"{description} [x, y, z] in millimetres, in the part's own frame.",
    }


def point2(description: str) -> dict[str, Any]:
    """A (u, v) position in a sketch's own 2D frame, in millimetres."""
    return {
        "type": "array",
        "minItems": 2,
        "maxItems": 2,
        "items": {
            "type": "number",
            "minimum": limits.MIN_COORD_MM,
            "maximum": limits.MAX_COORD_MM,
        },
        "description": (
            f"{description} [u, v] in millimetres, in the sketch's own 2D frame — "
            "u is the sketch's horizontal axis, v its vertical."
        ),
    }


def direction3(description: str) -> dict[str, Any]:
    """A direction vector. Need not be normalised; zero-length is refused."""
    return {
        "type": "array",
        "minItems": 3,
        "maxItems": 3,
        "items": {"type": "number", "minimum": -1e6, "maximum": 1e6},
        "description": (
            f"{description} [x, y, z]; length is ignored, only the direction is used. "
            "All three components zero is refused."
        ),
    }


def point_list(description: str, *, minimum: int = 2) -> dict[str, Any]:
    """An ordered run of 2D sketch points — a polyline or a spline."""
    return {
        "type": "array",
        "minItems": minimum,
        "maxItems": limits.MAX_POINTS,
        "items": point2("One vertex."),
        "description": description,
    }


def name_list(description: str, *, minimum: int = 1) -> dict[str, Any]:
    """A list of existing element names."""
    return {
        "type": "array",
        "minItems": minimum,
        "maxItems": limits.MAX_SELECTION,
        "items": {"type": "string", "minLength": 1, "maxLength": limits.MAX_NAME_CHARS},
        "description": description,
    }


def name_pair(description: str) -> dict[str, Any]:
    """Exactly two element names.

    Its own constructor rather than `name_list(minimum=2)` because "exactly
    two" is a different statement from "at least two": a corner between three
    elements is not a corner, and the schema should say so rather than let the
    daemon discover it.
    """
    return {
        "type": "array",
        "minItems": 2,
        "maxItems": 2,
        "items": {"type": "string", "minLength": 1, "maxLength": limits.MAX_NAME_CHARS},
        "description": description,
    }


def bounded_number(
    description: str, *, minimum: float, maximum: float, unit: str = ""
) -> dict[str, Any]:
    """A quantity whose bounds are domain-specific — pressure, temperature, force."""
    suffix = f" {unit}." if unit else ""
    return {
        "type": "number",
        "minimum": minimum,
        "maximum": maximum,
        "description": f"{description}{suffix}",
    }


def raw(schema: dict[str, Any]) -> dict[str, Any]:
    """An escape hatch for a shape none of the constructors above describe.

    If you use this twice for the same kind of value, write the constructor
    instead — that is how this module stays the vocabulary rather than becoming
    a pile of inline dicts with a helper on top.
    """
    if "description" not in schema:
        raise ValueError("raw() schemas still need a description; the model reads it.")
    return dict(schema)


def required(name: str, schema: dict[str, Any]) -> Param:
    return Param(name=name, schema=schema, required=True)


def optional(name: str, schema: dict[str, Any]) -> Param:
    return Param(name=name, schema=schema, required=False)


def from_server(name: str, schema: dict[str, Any]) -> Param:
    """A value the server computes and the model is not allowed to supply."""
    return Param(name=name, schema=schema, required=True, supplied_by_server=True)


def for_server(name: str, schema: dict[str, Any]) -> Param:
    """A value the model must supply and the server consumes rather than forwards."""
    return Param(name=name, schema=schema, required=True, consumed_by_server=True)
