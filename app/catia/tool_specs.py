"""The CATIA tool vocabulary the agent may call.

**This module is now a projection, not a source.** Every operation is declared
once in `app.catia.ops`, in the domain module it belongs to, and this file turns
those declarations into the `CatiaToolSpec` shape the agent layer consumes. It
was previously the first of four hand-written copies of the same facts — the
others being the daemon's validation table, the backend's abstract methods and
the two backend implementations — and keeping four files in step by hand is why
the tool count stopped at 39 while CATIA has 900+ commands.

To add a tool, add an `Operation` to the right module under `app/catia/ops/`.
Nothing here changes.

The three rules that shaped the vocabulary still hold, and are enforced by the
constructors in `ops.spec` rather than by convention:

**Semantic operations over raw coordinates where a name will do.** A model names
a plane, a face and a dimension. Coordinates exist now where they are genuinely
needed — a hole at a real position, a spline through real points — because the
alternative was a tool set that could only build parts centred on the origin.
What has not changed is that the coordinate frame maths lives inside the tool
implementation, where it is tested, not in the model's head.

**No filesystem paths, and no arbitrary execution.** The model names documents,
never paths — the daemon resolves everything inside its own working directory.
`SystemService.Evaluate`, CATIA's arbitrary-VBScript hatch, is not exposed and
must never be added: it would turn one prompt injection into remote code
execution on an engineer's workstation.

**Descriptions are prompt text.** The model reads them to choose, so they say
*when* to reach for a tool and what to do afterwards, not merely what it does.

The tier decides enforcement, not the UI:

* `read` runs freely.
* `write` needs `allow_mutations` on the turn and is auto-checkpointed first.
* `destructive` additionally needs a per-call approval token the server signs
  only after an explicit user click.

The daemon carries its own generated copy of the names, schemas and tiers and
re-validates every incoming call against it. A confused or compromised agent
stream cannot escalate by lying about a tier, and cannot smuggle an extra field
past a schema.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.catia.ops import OPERATIONS
from app.catia.ops.infrastructure import MATERIAL_KEYS
from app.catia.ops.spec import Tier
from app.catia.ops.vocabulary import (
    EDGE_SELECTORS,
    FACE_POSITIONS,
    NAMED_FACES,
    ORIGIN_PLANES,
    PARAMETER_UNITS,
    VIEWPOINTS,
)

#: CATIA's three standard reference planes. Kept under the old name because
#: call sites and tests use it; `ops.vocabulary.ORIGIN_PLANES` is the same tuple
#: and is what new code should read.
SKETCH_PLANES = ORIGIN_PLANES

#: Which part axes a pattern runs along when it is drawn in a named plane.
#: A pattern takes both its directions from one plane -- the first in-plane
#: axis, then the second -- so naming the plane names the directions, and the
#: model never handles a vector. Measured on a live V5-R33.
PATTERN_PLANE_AXES = {"XY": ("X", "Y"), "YZ": ("Y", "Z"), "ZX": ("Z", "X")}

__all__ = [
    "CATIA_TOOL_SPECS",
    "EDGE_SELECTORS",
    "FACE_POSITIONS",
    "MATERIAL_KEYS",
    "NAMED_FACES",
    "PARAMETER_UNITS",
    "PATTERN_PLANE_AXES",
    "SKETCH_PLANES",
    "TOOL_SPECS_BY_NAME",
    "VIEWPOINTS",
    "CatiaTier",
    "CatiaToolSpec",
    "get_spec",
]


class CatiaTier(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class CatiaToolSpec:
    """One callable CATIA operation, as the agent layer sees it.

    `parameters` is a JSON Schema object with `additionalProperties: false`.
    Strictness is the point: an unknown field is a model that has misunderstood
    the tool, and letting it through means the daemon silently ignores whatever
    the model actually meant.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    tier: CatiaTier
    #: Uses `catia_export_timeout_s` instead of `catia_call_timeout_s`. A STEP
    #: export re-tessellates the whole part and legitimately takes minutes.
    long_running: bool = False

    @property
    def mutating(self) -> bool:
        return self.tier is not CatiaTier.READ


#: The registry's tier enum and this one carry the same values by design; the
#: two exist separately so `ops` has no import edge back into its consumer.
_TIERS: dict[Tier, CatiaTier] = {
    Tier.READ: CatiaTier.READ,
    Tier.WRITE: CatiaTier.WRITE,
    Tier.DESTRUCTIVE: CatiaTier.DESTRUCTIVE,
}

CATIA_TOOL_SPECS: list[CatiaToolSpec] = [
    CatiaToolSpec(
        name=operation.name,
        description=operation.summary,
        parameters=operation.json_schema(),
        tier=_TIERS[operation.tier],
        long_running=operation.long_running,
    )
    for operation in OPERATIONS
]

TOOL_SPECS_BY_NAME: dict[str, CatiaToolSpec] = {spec.name: spec for spec in CATIA_TOOL_SPECS}


def get_spec(tool: str) -> CatiaToolSpec | None:
    return TOOL_SPECS_BY_NAME.get(tool)
