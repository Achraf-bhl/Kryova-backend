"""The tools the agent may call, and the dispatcher that runs them.

Three rules shape this file.

**Every tool is scoped to one user.** A tool never takes an owner id from the
model -- the dispatcher is constructed with the authenticated user and filters
on it. A hallucinated project id therefore returns "not found", never someone
else's data, exactly like the HTTP layer.

**Mutating tools are marked.** `run_simulation` burns real compute,
`delete_simulation` destroys results, and the CATIA write and destructive tiers
change a document on the user's workstation, so all of them carry
`mutating = True`. The agent loop refuses to run those unless the caller passed
`allow_mutations`, which the API only sets when the user has confirmed.
Read-only tools run freely.

**A tool does what its description says it does.** `run_simulation` used to
validate a load case and return `ready_to_submit`, expecting an API layer that
never consumed it -- so the agent told users their analysis was running while
nothing had been queued. A tool that describes an effect must produce it; if it
cannot, it raises so the model sees the failure.

Tool descriptions are prompt text: the model reads them to decide what to call,
so they say *when* to use a tool, not just what it does.
"""

import difflib
import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.resume import HISTORY_PAGE_LIMIT, build_history
from app.ai.state import bound_document_name
from app.catia_kb import catia_knowledge
from app.core.config import settings
from app.geometry import backends
from app.geometry.formats import GEOMETRY_FORMATS
from app.jobs import JobQueue
from app.media import LocalMediaStore, MediaService
from app.mesh.types import MeshError
from app.models import (
    Conversation,
    ConversationMessage,
    GeometryVersion,
    JobStatus,
    MessageRole,
    Project,
    SimulationJob,
    User,
)
from app.retrieval import knowledge_service
from app.simulation.limits import check_mesh_request
from app.simulation.runner import SessionScope, run_simulation
from app.solve.linear_static import LinearStaticSolver
from app.solve.materials import MATERIALS
from app.solve.types import LoadCase

logger = logging.getLogger(__name__)


class ToolError(RuntimeError):
    """A tool failed in a way the model should see and work around."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    mutating: bool = False

    def schema(self) -> dict[str, Any]:
        """OpenAI/Ollama function shape. Providers translate from here."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


#: Human labels for the step list in the UI. A user reading "list_geometry" has
#: to decode it; "Checking geometry versions" they can just read. One table, so
#: the live SSE stream and a rehydrated transcript cannot label the same step
#: two different ways.
BUILTIN_TOOL_LABELS: dict[str, str] = {
    "create_project": "Creating the project",
    "list_projects": "Looking up your projects",
    "get_project": "Reading the project",
    "update_project": "Renaming the project",
    "delete_project": "Deleting the project",
    "list_materials": "Checking the material library",
    # Reads the server's own log of CATIA calls, so it deliberately carries no
    # `catia_` prefix: that prefix means "goes to the workstation", and this one
    # answers with CATIA closed. `tests/test_tool_registry.py` enforces it.
    "design_history": "Reading what was already built",
    # Says what it produces — the specification — rather than that a row was
    # written. A user watching the step list should read that the part is being
    # written down, which is the thing that is actually happening.
    "record_design": "Writing down the design",
    "read_design": "Reading the design",
    "set_design_parameter": "Changing a design parameter",
    # The plan and its checkpoints (E16 tasks 2, 5, 6). Each says what is
    # happening to the *work*, not which module answered.
    "plan_work": "Planning the work",
    "update_task": "Updating the plan",
    "request_approval": "Asking you to sign this off",
    "estimate_cost": "Checking what this will cost",
    # Says what it is for, not which module answers. A user watching the step
    # list should read that the work is being checked.
    "check_part": "Checking the part against the request",
    # Deliberately says what the assistant is doing, not how it does it. A user
    # watching the step list should read "it went and checked the manuals",
    # which is the true and useful description; the retrieval mechanism behind
    # it is an implementation detail and naming it here would be noise.
    "search_documentation": "Checking the documentation",
    # Same register as the line above: the user is told what is being
    # consulted, not which of two lookup mechanisms answered.
    "explain_catia_term": "Checking the CATIA reference",
    "list_geometry": "Checking geometry versions",
    "list_simulations": "Reviewing previous runs",
    "get_simulation": "Reading the simulation result",
    # Says what it produces, not how. A user watching the step list should
    # read that the loading is being worked out, which is the true and useful
    # description; that a model call turns the sentence into a load case is an
    # implementation detail.
    "draft_load_case": "Working out the loads",
    "run_simulation": "Submitting the analysis",
    "delete_simulation": "Deleting the run",
    # The direct-COM tools. They carry no `catia_` prefix, so `catia_label`
    # never sees them and an unlisted name would render as "open in catia".
    "open_in_catia": "Opening CATIA",
    "sync_geometry_from_catia": "Importing geometry from CATIA",
}


def catia_label(name: str) -> str:
    """A readable label for a bridge tool, derived from its name.

    Mostly generated rather than tabulated: the tool vocabulary lives in the
    bridge package, and a hand-written table here would silently fall behind it.
    The handful of names worth phrasing better are spelled out.
    """
    special = {
        "catia_status": "Checking the CATIA bridge",
        "catia_new_part": "Creating a CATIA part",
        "catia_open_document": "Reopening the CATIA document",
        "catia_export_step": "Exporting STEP to Kryova",
        "catia_capture_view": "Looking at the part",
        "catia_measure": "Measuring the part",
    }
    if name in special:
        return special[name]
    return "CATIA: " + name.removeprefix("catia_").replace("_", " ")


def tool_label(name: str) -> str:
    """Label one tool by name, without needing a live `ToolBox`.

    The conversation read endpoint rehydrates a transcript long after the
    toolbox that ran it is gone, and it must produce the same labels.
    """
    if name in BUILTIN_TOOL_LABELS:
        return BUILTIN_TOOL_LABELS[name]
    if name.startswith("catia_"):
        return catia_label(name)
    return name.replace("_", " ")


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# CATIA. Imported lazily and defensively: the bridge package ships separately,
# and this module must import cleanly without it.
# ---------------------------------------------------------------------------

#: CATIA tools that can run before a document is bound to the conversation.
#: Everything else needs one, and saying so in a tool error is what turns the
#: binding rule from prompt guidance into something the model cannot skip -- and
#: does it without a round trip to a workstation that would only answer "no
#: active document".
CATIA_NO_DOCUMENT_REQUIRED = frozenset(
    {"catia_status", "catia_new_part", "catia_product_create", "catia_open_document"}
)

#: Result keys worth caching on the conversation for the next turn's state
#: block. Everything else a tool returns is either transient or already in the
#: transcript.
CATIA_STATE_KEYS = (
    "features",
    "parameters",
    "material",
    "density_kg_m3",
    "mass_kg",
    "volume_mm3",
    "bounding_box_mm",
    "centre_of_gravity_mm",
    "center_of_gravity_mm",
)


def _catia_dispatch() -> Any | None:
    """The bridge dispatcher, or None when CATIA is off or not installed.

    Returning None rather than raising is what lets the same agent serve a user
    with no Windows workstation: the tools simply are not in its vocabulary, so
    it never offers a capability that cannot work.
    """
    if not settings.catia_enabled:
        return None
    try:
        from app.catia import dispatch
    except Exception:  # noqa: BLE001 - optional package, built in parallel
        return None
    return dispatch


#: A load case that is definitely valid, in the fewest words. Shown in the
#: refusal because the shape is the hard part: a model that gets it wrong does
#: not need to be told it is wrong, it needs to see one that is right.
_LOAD_CASE_EXAMPLE: Final[str] = (
    '{"name": "Tip load", '
    '"material": {"name": "steel", "youngs_modulus_mpa": 210000, "poissons_ratio": 0.3, '
    '"density_kg_m3": 7850, "yield_strength_mpa": 250}, '
    '"fixtures": [{"where": {"type": "face", "axis": "x", "side": "min"}}], '
    '"loads": [{"type": "force", "where": {"type": "face", "axis": "x", "side": "max"}, '
    '"force_n": [0, 0, -500]}]}'
)


def _load_case_problem(error: ValidationError) -> str:
    """What is wrong with a load case, in words, with a working example.

    Pydantic's own text was what reached the model before:

        That load case is not valid: 3 validation errors for LoadCase
        material
          Field required [type=missing, input_value={...}, input_type=dict]
            For further information visit https://errors.pydantic.dev/2.13/v/missing

    Measured on ladder prompt H4, 2026-09-06: the agent read that four times,
    sent four more variations, and never produced a valid case -- so a bracket
    that was correctly built was never analysed, on a prompt whose whole point
    is the analysis. The failure is not that the model is small. Every line of
    that message is about pydantic; none of it says what a load case *is*, and
    the one thing that would fix it in a single round -- an example of the
    right shape -- was nowhere.

    So: the field paths, said plainly, and one valid case in full. Truncated
    to the first few, because a model that got the shape wrong has one problem
    and not seven, and a wall of them buries the example underneath.
    """
    missing: list[str] = []
    wrong: list[str] = []
    for item in error.errors():
        where = ".".join(str(part) for part in item.get("loc", ())) or "the load case"
        if item.get("type") == "missing":
            missing.append(where)
        else:
            wrong.append(f"{where} ({item.get('msg', 'is not valid')})")

    parts = ["That load case cannot be run."]
    if missing:
        parts.append("Missing: " + ", ".join(missing[:6]) + ".")
    if wrong:
        parts.append("Wrong: " + "; ".join(wrong[:4]) + ".")
    parts.append(
        "A load case needs a name, a material with its properties, at least one "
        "fixture and at least one load; a fixture and a load are each a `where` "
        "selector plus their values, and a force is a vector in newtons. "
        f"This one is valid: {_LOAD_CASE_EXAMPLE}"
    )
    parts.append(
        "Or call draft_load_case with the loading in plain words and it will "
        "build one against this part's real bounding box."
    )
    return " ".join(parts)


@dataclass
class ToolBox:
    """The tool set bound to one user and one database session."""

    db: Session
    user: User
    #: Set when the conversation is scoped to a project, so the model can say
    #: "the latest run" without repeating the id every turn.
    project_id: str | None = None
    #: The conversation being served. Needed for the CATIA document binding --
    #: a conversation owns at most one document, and the tools are what record
    #: and enforce that.
    conversation: Conversation | None = None
    #: Injected so `run_simulation` can actually submit. Absent in contexts that
    #: have no queue (a scripted test of read-only behaviour); the tool then
    #: refuses rather than pretending.
    job_queue: JobQueue | None = None
    session_scope: SessionScope | None = None
    media_store: LocalMediaStore | None = None
    media: MediaService | None = None
    #: Injected so `draft_load_case` can turn a sentence into a load case. The
    #: same provider serving the conversation, so a draft costs one model call
    #: on the machine already answering. Absent in contexts with no model; the
    #: tool is then withheld rather than offered and refused.
    provider: Any = None
    _tools: dict[str, Tool] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        for tool in [
            *self._build(),
            *self._build_catia(),
            *self._build_knowledge(),
            *self._build_catia_reference(),
        ]:
            self._tools[tool.name] = tool

    # -- lookup helpers -----------------------------------------------------

    def _project(self, project_id: str | None) -> Project:
        resolved = project_id or self.project_id
        if not resolved:
            raise ToolError(
                "No project specified and this conversation is not scoped to one. "
                "Call list_projects and ask the user which one they mean."
            )
        project = self.db.get(Project, resolved)
        # Same 404-not-403 posture as the HTTP layer: never confirm that an id
        # exists for a project the user does not own.
        if project is None or project.owner_id != self.user.id:
            raise ToolError(f"No project with id {resolved!r} belongs to you.")
        return project

    # -- the tools ----------------------------------------------------------

    def _build(self) -> list[Tool]:
        tools: list[Tool] = [
            Tool(
                name="create_project",
                description=(
                    "Create a new, empty project and make it the conversation's current "
                    "project. Call this once, at the start of a new-project conversation, "
                    "as soon as you know what the user is working on -- a short name is "
                    "enough to begin; the geometry comes afterwards. Do not call it again "
                    "in the same conversation, and do not call it to 'reset' a project."
                ),
                parameters=_object(
                    {
                        "name": {
                            "type": "string",
                            "description": (
                                "Short human name for the part or assembly, e.g. "
                                "'Bracket assembly'. Max 255 characters."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": "One line on what it is or what it must carry.",
                        },
                    },
                    required=["name"],
                ),
                handler=self._create_project,
            ),
            Tool(
                name="design_history",
                description=(
                    "Everything this conversation has already done in CATIA, in the "
                    "order it happened: which tool ran, what it acted on, whether it "
                    "worked and what it said when it did not. Read from the server's "
                    "own log of the calls, so it is complete even for work that "
                    "happened days ago and is no longer in the conversation above you. "
                    "Call this when you are picking a conversation back up, when the "
                    "state block says an operation is unfinished, or before redoing "
                    "anything -- it is how you find out whether a feature was already "
                    "built rather than asking the user to remember. Needs no "
                    "workstation: it answers even when CATIA is closed."
                ),
                parameters=_object(
                    {
                        "failures_only": {
                            "type": "boolean",
                            "description": (
                                "Only the calls that failed. Use this to pick up loose "
                                "ends without reading the whole build order."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                f"How many operations to return, newest kept, "
                                f"1-{HISTORY_PAGE_LIMIT}. Defaults to "
                                f"{HISTORY_PAGE_LIMIT}."
                            ),
                        },
                    }
                ),
                handler=self._design_history,
            ),
            Tool(
                name="check_part",
                description=(
                    "Measure the part you have built and check it against what was "
                    "asked for. Call this after finishing a part, before telling the "
                    "user it is done.\n"
                    "Give one claim per requirement, in numbers: the plate is 100 mm "
                    "wide, it weighs no more than 2 kg, it is one solid. Each claim "
                    "names a `measure` -- a path into the measurement, such as "
                    "mass_kg, volume_mm3, solid_count, face_count, "
                    "bounding_box_mm.size[0] or centre_of_mass_mm[2] -- a comparison "
                    "(<=, >=, <, >, ==, !=) and a bound. An `==` on a measured number "
                    "needs a tolerance; a kernel does not return round decimals.\n"
                    "A claim that could not be measured comes back UNMEASURED, which "
                    "is NOT a pass: it means nobody checked, and you must say so "
                    "rather than reporting the part as verified. Volume and mass are "
                    "the two that catch the most, because a feature that went in "
                    "wrong almost always moves one of them."
                ),
                parameters=_object(
                    {
                        "claims": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 40,
                            "description": (
                                "One entry per requirement you are checking."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": (
                                            "What this claim says, in words -- it is "
                                            "what a failure is reported under."
                                        ),
                                    },
                                    "measure": {
                                        "type": "string",
                                        "description": (
                                            "Path into the measurement, e.g. mass_kg "
                                            "or bounding_box_mm.size[2]."
                                        ),
                                    },
                                    "comparison": {
                                        "type": "string",
                                        "enum": ["<=", ">=", "<", ">", "==", "!="],
                                    },
                                    "bound": {
                                        "type": "number",
                                        "description": "The value to compare against.",
                                    },
                                    "tolerance": {
                                        "type": "number",
                                        "description": (
                                            "Slack in the measurement's own unit. "
                                            "Required for ==."
                                        ),
                                    },
                                },
                                "required": [
                                    "name",
                                    "measure",
                                    "comparison",
                                    "bound",
                                ],
                                "additionalProperties": False,
                            },
                        }
                    },
                    ["claims"],
                ),
                handler=self._check_part,
            ),
            Tool(
                name="record_design",
                description=(
                    "Write down the part as a specification: the parameters it is "
                    "built from, the features it is made of, and why each one is "
                    "there. This is the design itself -- the conversation is only "
                    "the log of it -- and it is what the user sees beside the chat, "
                    "what a reviewer signs off, and what a later change is diffed "
                    "against.\n"
                    "Call it once you know the shape of the part, and again whenever "
                    "the design changes. Pass the WHOLE design every time, not a "
                    "patch: what you send replaces what was stored, and anything you "
                    "leave out is gone from the part.\n"
                    "Give a dimension a parameter and refer to it, rather than "
                    "repeating a number in four features -- that is what makes "
                    "'make it 20% thicker' one edit instead of four. An argument "
                    "starting '=' is a formula over the parameters; one starting '@' "
                    "refers to another feature in this design by name.\n"
                    "Recording a design does NOT build it. It is the description; "
                    "the CATIA or kernel tools are what make geometry."
                ),
                parameters=_object(
                    {
                        "name": {
                            "type": "string",
                            "description": (
                                "The part's name, e.g. 'motor_bracket'. Becomes the "
                                "CATIA document name."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": "One or two lines on what this part is for.",
                        },
                        "material": {
                            "type": "string",
                            "description": (
                                "Material key from list_materials, e.g. 'steel_s235'. "
                                "Omit for a geometry-only design; a wrong key comes "
                                "back as a mass roughly three times out, not an error."
                            ),
                        },
                        "parameters": {
                            "type": "array",
                            "description": (
                                "The named decisions the part is built from. Each has "
                                "a name and either a value or an expression -- never "
                                "both. A parameter with an expression is a consequence "
                                "of the others and must not be given a number."
                            ),
                            "items": _object(
                                {
                                    "name": {"type": "string"},
                                    "value": {"type": "number"},
                                    "expression": {
                                        "type": "string",
                                        "description": (
                                            "A formula over the other parameters, "
                                            "without a leading '=', e.g. 'wall_mm * 2'."
                                        ),
                                    },
                                    "unit": {
                                        "type": "string",
                                        "description": (
                                            "mm, deg, kg, mm2, mm3 or empty for a "
                                            "count or a ratio."
                                        ),
                                    },
                                    "description": {"type": "string"},
                                },
                                required=["name"],
                            ),
                        },
                        "features": {
                            "type": "array",
                            "description": (
                                "What the part is made of, IN BUILD ORDER -- a pad "
                                "cannot come before the sketch it extrudes. Order is "
                                "kept exactly as given."
                            ),
                            "items": _object(
                                {
                                    "name": {
                                        "type": "string",
                                        "description": (
                                            "A semantic name, e.g. 'plate.profile' or "
                                            "'web.pad'. This is how other features "
                                            "refer to it, so it must not change when "
                                            "the geometry does."
                                        ),
                                    },
                                    "op": {
                                        "type": "string",
                                        "description": (
                                            "The operation that makes it, e.g. "
                                            "'catia_pad'."
                                        ),
                                    },
                                    "args": {
                                        "type": "object",
                                        "description": (
                                            "The operation's arguments. '=formula' for "
                                            "an expression, '@other.feature' for a "
                                            "reference."
                                        ),
                                    },
                                    "when": {
                                        "type": "string",
                                        "description": (
                                            "A condition, without a leading '='. The "
                                            "feature is skipped when it is false, and "
                                            "stays in the design -- which is how a "
                                            "part has an optional pocket without "
                                            "having two half-maintained designs."
                                        ),
                                    },
                                    "note": {
                                        "type": "string",
                                        "description": (
                                            "Why this feature exists. Answer it for "
                                            "somebody reading in six months, not for "
                                            "yourself now."
                                        ),
                                    },
                                },
                                required=["name", "op"],
                            ),
                        },
                    },
                    required=["name"],
                ),
                handler=self._record_design,
                mutating=True,
            ),
            Tool(
                name="read_design",
                description=(
                    "The part's specification as it currently stands, with its "
                    "revision history: what changed at each step and who changed it. "
                    "Call this when picking a conversation back up, before editing a "
                    "design you did not write in this turn, or when the user asks "
                    "what a parameter is set to. It answers from the server's own "
                    "record, so it is right even for work from days ago that is no "
                    "longer in the conversation above you."
                ),
                parameters=_object({}),
                handler=self._read_design,
            ),
            Tool(
                name="set_design_parameter",
                description=(
                    "Change one number in the design and find out what it reaches. "
                    "Use this for 'make the wall 8 mm' rather than re-sending the "
                    "whole design: it is one edit, it records what moved, and it "
                    "comes back telling you which features have to be rebuilt -- "
                    "INCLUDING the ones nobody edited that stand on something that "
                    "was, which are the ones that surprise people.\n"
                    "A parameter that is derived from a formula is refused: change "
                    "one of the parameters its formula reads instead."
                ),
                parameters=_object(
                    {
                        "name": {
                            "type": "string",
                            "description": "The parameter to change.",
                        },
                        "value": {"type": "number", "description": "Its new value."},
                    },
                    required=["name", "value"],
                ),
                handler=self._set_design_parameter,
                mutating=True,
            ),
            Tool(
                name="plan_work",
                description=(
                    "Write down the plan for a job that takes several stages: the "
                    "steps, what each one waits on, and which of them a person has "
                    "to sign off before the work goes past it.\n"
                    "Use it for a machine, an assembly, or anything you expect to "
                    "take more than a handful of operations. Do NOT use it for a "
                    "single part you can build in a few calls — a plan for that is "
                    "overhead, and the plan is meant to be read.\n"
                    "The server holds the plan and enforces its order: a step "
                    "cannot be marked done while something it waits on is not, and "
                    "it will tell you which. Passing the whole plan replaces the "
                    "one stored, so send it whole when the shape of the work "
                    "changes and use update_task for a step moving along."
                ),
                parameters=_object(
                    {
                        "tasks": {
                            "type": "array",
                            "description": (
                                "The steps, in the order you intend to do them. "
                                "Dependencies decide the real order; this order only "
                                "breaks ties."
                            ),
                            "items": _object(
                                {
                                    "id": {
                                        "type": "string",
                                        "description": (
                                            "Short and stable, e.g. 'frame' or "
                                            "'ram_sizing'. Other steps depend on it "
                                            "by this id."
                                        ),
                                    },
                                    "title": {
                                        "type": "string",
                                        "description": "What this step is, for a person to read.",
                                    },
                                    "depends_on": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "Ids of steps that must be finished (or "
                                            "deliberately skipped) first."
                                        ),
                                    },
                                    "checkpoint": {
                                        "type": "boolean",
                                        "description": (
                                            "True when a person has to approve this "
                                            "step before anything after it proceeds. "
                                            "Use it where a wrong answer is expensive "
                                            "to undo: a sizing decision, a material "
                                            "choice, anything that will be "
                                            "manufactured."
                                        ),
                                    },
                                }
                            ),
                        }
                    },
                    required=["tasks"],
                ),
                handler=self._plan_work,
                mutating=True,
            ),
            Tool(
                name="update_task",
                description=(
                    "Move one step of the plan along. Mark it active when you start "
                    "it, done when it is finished and checked, blocked when "
                    "something outside your control is stopping it, or skipped when "
                    "the design went a different way and it is no longer needed.\n"
                    "A step cannot be marked done while something it waits on is "
                    "not — you will be told which, and that is the answer, not an "
                    "error to retry around. Blocked and skipped are different: "
                    "blocked means say so and move on, skipped means it is settled "
                    "and the work after it may proceed."
                ),
                parameters=_object(
                    {
                        "id": {"type": "string", "description": "The step's id."},
                        "state": {
                            "type": "string",
                            "enum": ["pending", "active", "done", "blocked", "skipped"],
                        },
                        "note": {
                            "type": "string",
                            "description": (
                                "Why. Required in spirit for blocked and skipped: a "
                                "step that is blocked by nothing anybody wrote down "
                                "cannot be unblocked by anybody else."
                            ),
                        },
                    },
                    required=["id", "state"],
                ),
                handler=self._update_task,
                mutating=True,
            ),
            Tool(
                name="request_approval",
                description=(
                    "Ask a person to sign off on something before you go further. "
                    "Use it at a checkpoint in the plan, and any time you are about "
                    "to do something expensive to undo that the user has not "
                    "explicitly asked for.\n"
                    "This ENDS YOUR TURN. The question and what you are showing are "
                    "recorded as a gate the user can approve or reject; nothing "
                    "waits on you in the meantime and nothing is lost. Say clearly "
                    "in your message what you are asking and why, because that is "
                    "what they will read.\n"
                    "It is pinned to what you show it: if the design moves while the "
                    "gate is open, the approval will not apply to the new one. That "
                    "is deliberate — an approval of 'whatever it becomes' is not a "
                    "sign-off."
                ),
                parameters=_object(
                    {
                        "title": {
                            "type": "string",
                            "description": "A few words naming the decision.",
                        },
                        "question": {
                            "type": "string",
                            "description": (
                                "What you are asking, in full. State the options and "
                                "what each one costs — a reviewer with one option is "
                                "being told, not asked."
                            ),
                        },
                        "task_id": {
                            "type": "string",
                            "description": (
                                "The checkpoint in the plan this is for, if there is "
                                "one."
                            ),
                        },
                    },
                    required=["title", "question"],
                ),
                handler=self._request_approval,
                mutating=True,
            ),
            Tool(
                name="estimate_cost",
                description=(
                    "What a simulation is likely to cost this account, before you "
                    "start one. Call it before submitting a run that is large or "
                    "that the user did not explicitly ask for — a convergence "
                    "study, a fine mesh, a sweep.\n"
                    "The number comes from this account's own recorded runs, so a "
                    "new account gets told there is not enough history rather than "
                    "a made-up figure. Report what it says; do not turn 'we cannot "
                    "estimate this yet' into a guess."
                ),
                parameters=_object({}),
                handler=self._estimate_cost,
            ),
            Tool(
                name="list_projects",
                description=(
                    "List the user's projects with their ids. Call this first whenever "
                    "the user names a project in words rather than by id, or when you "
                    "need to know what exists."
                ),
                parameters=_object({}),
                handler=self._list_projects,
            ),
            Tool(
                name="get_project",
                description=(
                    "Full detail for one project: name, description, when it was created "
                    "and updated, how many geometry versions and simulations it holds, "
                    "and the newest geometry version. Use this to answer 'what is in this "
                    "project' in one call instead of three."
                ),
                parameters=_object(
                    {
                        "project_id": {
                            "type": "string",
                            "description": "Defaults to the conversation's project.",
                        }
                    }
                ),
                handler=self._get_project,
            ),
            Tool(
                name="update_project",
                description=(
                    "Rename a project or change its description. Pass only the fields to "
                    "change; anything omitted is left alone. Use this when the user asks "
                    "to rename or re-describe a project."
                ),
                parameters=_object(
                    {
                        "project_id": {
                            "type": "string",
                            "description": "Defaults to the conversation's project.",
                        },
                        "name": {"type": "string", "description": "New name, 1-255 characters."},
                        "description": {
                            "type": "string",
                            "description": "New description. Pass an empty string to clear it.",
                        },
                    }
                ),
                # Renaming is reversible and destroys nothing, so it stays
                # ungated for the same reason `create_project` does.
                handler=self._update_project,
            ),
            Tool(
                name="delete_project",
                description=(
                    "Permanently delete a project and everything in it -- every geometry "
                    "version, every simulation and every stored file. This cannot be "
                    "undone. Confirm the project's name with the user before calling it, "
                    "and never call it to 'clean up' on your own initiative."
                ),
                parameters=_object(
                    {
                        "project_id": {
                            "type": "string",
                            "description": "Defaults to the conversation's project.",
                        }
                    },
                ),
                mutating=True,
                handler=self._delete_project,
            ),
            Tool(
                name="list_materials",
                description=(
                    "The material library, with Young's modulus, Poisson's ratio, yield "
                    "strength and density. Call before building a load case so you use a "
                    "real library name rather than inventing property values."
                ),
                parameters=_object({}),
                handler=self._list_materials,
            ),
            Tool(
                name="list_geometry",
                description=(
                    "Geometry versions for a project, newest first, with each one's "
                    "bounding box. Call this before drafting a load case -- the bounding "
                    "box is what turns 'the top face' into an axis and a side."
                ),
                parameters=_object(
                    {
                        "project_id": {
                            "type": "string",
                            "description": "Omit to use the current project.",
                        }
                    }
                ),
                handler=self._list_geometry,
            ),
            Tool(
                name="list_simulations",
                description=(
                    "Simulation runs for a project, newest first, with status and headline "
                    "results. Call this to answer 'how did the last run go' or to check "
                    "whether the analysis the user is asking for has already been done."
                ),
                parameters=_object(
                    {
                        "project_id": {"type": "string"},
                        "limit": {"type": "integer", "description": "Default 10, max 50."},
                    }
                ),
                handler=self._list_simulations,
            ),
            Tool(
                name="get_simulation",
                description=(
                    "Full detail for one run: status, load case, mesh statistics, full "
                    "result and any solver warnings. Call this to poll a run you just "
                    "submitted, and before interpreting a result or advising on a change."
                ),
                parameters=_object(
                    {"simulation_id": {"type": "string"}}, required=["simulation_id"]
                ),
                handler=self._get_simulation,
            ),
            Tool(
                name="draft_load_case",
                description=(
                    "Turn a sentence about how a part is loaded into a load case the "
                    "solver will accept, measured against this project's real geometry. "
                    "Say what is applied and where in ordinary words -- '500 N hanging "
                    "off the free end, bolted to the wall at the other, mild steel' -- "
                    "and it returns the load case plus the assumptions it had to make "
                    "and anything it could not resolve.\n"
                    "Use this rather than writing a load case by hand: the shape is "
                    "fiddly and this one is built against the part's own bounding box, "
                    "so 'the top face' and 'the far end' mean something. Say 'far end' "
                    "and 'near end' for the two ends of the part -- they resolve to "
                    "whichever axis it is longest in, so they are right on a beam lying "
                    "in any direction, where 'left' and 'right' are always the X faces "
                    "and would hold and load it across its own thickness. Show the user "
                    "the assumptions before running it -- they are the numbers you chose "
                    "and they did not, and read `unresolved`: a drafted case is checked "
                    "against the part and says there what looks wrong.\n"
                    "The part must have been exported to Kryova first "
                    "(catia_export_step or sync_geometry_from_catia); a load case cannot "
                    "be resolved against geometry that is only open in CATIA."
                ),
                parameters=_object(
                    {
                        "description": {
                            "type": "string",
                            "description": (
                                "How the part is loaded and held, in plain words. "
                                "Include the material if you know it."
                            ),
                        },
                        "project_id": {"type": "string"},
                        "geometry_version": {
                            "type": "integer",
                            "description": "Omit for the project's latest version.",
                        },
                    },
                    required=["description"],
                ),
                handler=self._draft_load_case,
            ),
            Tool(
                name="run_simulation",
                description=(
                    "Submit a linear static analysis. This consumes real compute and can "
                    "take minutes, so only call it once you have a complete load case and "
                    "the user has asked for the run. Returns immediately with a job id "
                    "and a status of 'queued' -- poll get_simulation for the outcome; do "
                    "not tell the user it succeeded until you have."
                ),
                parameters=_object(
                    {
                        "project_id": {"type": "string"},
                        "geometry_version": {
                            "type": "integer",
                            "description": "Omit for the project's latest version.",
                        },
                        "element_size_mm": {
                            "type": "number",
                            "description": (
                                "Target mesh size. Halving it multiplies element count by "
                                "about 8. Omit unless the user asked for a specific mesh."
                            ),
                        },
                        "element_order": {
                            "type": "integer",
                            "enum": [1, 2],
                            "description": (
                                "2 for quadratic tets (the default), 1 for linear. "
                                "Quadratic elements are far more accurate in bending at "
                                "the same element count, at roughly 2.5x the degrees of "
                                "freedom and solve time. Drop to 1 only for a quick shape "
                                "check on a chunky part: on anything slender or loaded in "
                                "bending, linear tets are far too stiff and their peak "
                                "stress is not reproducible between runs."
                            ),
                        },
                        "load_case": {
                            "type": "object",
                            "description": (
                                "Full load case: name, material (a library name plus its "
                                "properties), at least one fixture and at least one load. "
                                "Forces are total newtons; a downward 500 N is [0,0,-500]."
                            ),
                        },
                    },
                    required=["load_case"],
                ),
                handler=self._run_simulation,
                mutating=True,
            ),
            Tool(
                name="delete_simulation",
                description=(
                    "Permanently delete one finished simulation and its stored result "
                    "fields. Destructive and irreversible: describe exactly which run "
                    "will go and what it showed, and only call this after the user has "
                    "agreed. A run that is still queued or running cannot be deleted."
                ),
                parameters=_object(
                    {"simulation_id": {"type": "string"}}, required=["simulation_id"]
                ),
                handler=self._delete_simulation,
                mutating=True,
            ),
        ]
        # The direct-COM tools, for the deployment where the backend runs on the
        # same Windows box as CATIA. They sit alongside -- not instead of -- the
        # WebSocket bridge tools that `_build_catia` derives from
        # `CATIA_TOOL_SPECS`, which serve the workstation-daemon topology.
        #
        # `catia_status` is deliberately not among them: the spec table owns
        # every `catia_*` name, and `_build_catia` runs after this method, so a
        # second definition here would be built and then immediately shadowed.
        if _catia_dispatch() is not None:
            tools.extend(
                [
                    Tool(
                        name="open_in_catia",
                        description=(
                            "Start CATIA, bring its window to the screen, and open one fresh "
                            "empty part to build in. This is how a project gets its geometry in "
                            "this product: the part is modelled in CATIA rather than hunting for "
                            "a file to upload. Call it right after creating a project, once the "
                            "user has said what they are building. It creates and claims the "
                            "part itself, so the next call is the first modelling step "
                            "(catia_sketch_create, usually) -- not catia_new_part. Takes up to a "
                            "few minutes if CATIA is cold."
                        ),
                        parameters=_object(
                            {
                                "new_part": {
                                    "type": "boolean",
                                    "description": (
                                        "True to add a new empty CATPart. False to just bring up "
                                        "CATIA with whatever the user already has open."
                                    ),
                                },
                                "name": {
                                    "type": "string",
                                    "description": (
                                        "What to call the part, e.g. 'Titanium bracket'. Name it "
                                        "here rather than creating a second one later."
                                    ),
                                },
                            }
                        ),
                        handler=self._open_in_catia,
                        mutating=True,
                    ),
                    Tool(
                        name="sync_geometry_from_catia",
                        description=(
                            "Pull whatever part is currently active in CATIA into the project as "
                            "a new geometry version, exporting it to STEP on the way. This "
                            "replaces uploading a file by hand -- call it once the user says "
                            "their model is ready, and again after any change they want analysed. "
                            "Returns the new version number, which run_simulation can then use."
                        ),
                        parameters=_object(
                            {
                                "project_id": {"type": "string"},
                                "note": {
                                    "type": "string",
                                    "description": "Short note, e.g. 'after adding the fillet'.",
                                },
                            }
                        ),
                        handler=self._sync_geometry_from_catia,
                        mutating=True,
                    ),
                ]
            )
        return tools

    # -- handlers -----------------------------------------------------------

    def _create_project(self, name: str, description: str | None = None) -> dict[str, Any]:
        """Create a project and adopt it as the conversation's scope.

        Deliberately *not* marked mutating. That gate exists for tools that burn
        compute or destroy results; an empty project row is cheap and
        reversible, and gating it would mean a new-project chat has to ask
        permission for the thing the user just clicked a button to do. The
        expensive tools stay gated.
        """
        name = (name or "").strip()
        if not name:
            raise ToolError("A project needs a name. Ask the user what to call it.")
        # Mirrors ProjectCreate's bound rather than letting the DB truncate.
        if len(name) > 255:
            raise ToolError("That name is too long; keep it under 255 characters.")

        # One project per conversation, enforced rather than merely asked for.
        # The tool description already says "Do not call it again in the same
        # conversation", and a weak model ignores it: observed creating "Steel
        # mounting bracket" on turn one and then "Flat Plate" on turn two, when
        # the user had only ever described one part. The second project silently
        # became the conversation's scope, so the geometry, the runs and the
        # results all landed somewhere the user was not looking.
        #
        # An error rather than a silent no-op, because the model has to know
        # which project it is in to carry on correctly -- and the id is right
        # here in the message.
        if self.project_id:
            existing = self.db.get(Project, self.project_id)
            if existing is not None and existing.owner_id == self.user.id:
                raise ToolError(
                    f"This conversation is already working on project "
                    f"{existing.name!r} (id {existing.id}). Use it rather than "
                    "creating another; call update_project to rename it if the "
                    "user wants a different name."
                )

        project = Project(
            owner_id=self.user.id,
            name=name,
            description=(description or "").strip() or None,
        )
        self.db.add(project)
        self.db.flush()

        # Adopting the id here is what lets every later tool in this turn omit
        # project_id and still resolve -- see _project().
        self.project_id = project.id
        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "next_step": (
                "Tell the user the project exists. Geometry comes next: either they "
                "upload a CAD file (STEP, IGES or STL) -- you have no tool for that -- "
                "or you build it with the CATIA tools if they are available to you."
            ),
        }

    def _design_history(
        self, failures_only: bool = False, limit: int = HISTORY_PAGE_LIMIT
    ) -> dict[str, Any]:
        """Read this conversation's CATIA operation log.

        Scoped to the conversation and nothing else, which is also the access
        control: the log is keyed on `conversation_id`, and a conversation
        belongs to one user. There is no argument that could widen it.
        """
        conversation = self.conversation
        return build_history(
            self.db,
            conversation.id if conversation is not None else None,
            limit=limit,
            failures_only=bool(failures_only),
        )

    # -- the design record (P5.3, P5.6) -------------------------------------
    #
    # `app.design` is pure and must stay that way, so the imports below are
    # local to these three handlers rather than module-level: nothing about a
    # toolbox needs the compiler unless somebody actually records a design, and
    # keeping them here is what stops `app.ai.tools` from quietly becoming the
    # module that couples the agent to the design package's import graph.

    def _design_conversation(self) -> Conversation:
        conversation = self.conversation
        if conversation is None:
            raise ToolError(
                "There is no conversation to attach a design to. A design belongs to "
                "the conversation it was worked out in."
            )
        return conversation

    def _record_design(
        self,
        name: str,
        description: str = "",
        material: str | None = None,
        parameters: list[dict[str, Any]] | None = None,
        features: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Store the whole design, replacing whatever was there.

        Whole-document rather than a patch, and the tool description says so in
        capitals. A merge would need a rule for "the model omitted a feature" —
        deleted, or not mentioned? — and both readings are defensible, which
        means whichever one was chosen would be wrong roughly half the time and
        silently. Replacing has one reading.

        A spec that does not compile is refused *before* anything is written
        (`designs.save` diffs, which compiles both sides), and the compiler's
        own message comes back to the model. That message names the feature and
        says what is wrong with it, which is what the model needs in order to
        fix it on the next step rather than to guess.
        """
        from app.core import designs
        from app.design.errors import SpecError
        from app.design.params import Parameter, Unit
        from app.design.spec import DesignSpec, FeatureSpec

        conversation = self._design_conversation()
        try:
            built_parameters = [
                Parameter(
                    name=str(entry["name"]),
                    unit=Unit(str(entry.get("unit") or "")),
                    value=entry.get("value"),
                    expression=entry.get("expression"),
                    description=str(entry.get("description") or ""),
                )
                for entry in (parameters or [])
            ]
            spec = DesignSpec.of(
                name=name,
                parameters=built_parameters,
                features=[FeatureSpec.from_dict(entry) for entry in (features or [])],
                material=material,
                description=description,
            )
        except (SpecError, ValueError, KeyError, TypeError) as exc:
            raise ToolError(f"That design cannot be read: {exc}") from None

        try:
            outcome = designs.save(
                self.db, conversation, spec, author=designs.AUTHOR_AGENT, author_id=None
            )
        except SpecError as exc:
            raise ToolError(f"That design does not compile: {exc}") from None

        self.db.flush()
        result: dict[str, Any] = {
            "design": spec.name,
            "digest": outcome.document.digest,
            "revision": outcome.document.revision_number,
            "changed": outcome.changed,
            "parameters": list(spec.parameters.names()),
            "features": list(spec.feature_names()),
        }
        if not outcome.changed:
            # Said plainly rather than reported as a successful write. A model
            # told "saved" after a no-op edit has no way to notice that the
            # change it thought it made did not happen, and will report it to
            # the user as done.
            result["note"] = (
                "This is byte-identical to the design already stored, so nothing "
                "changed and no revision was written."
            )
        elif outcome.diff is not None:
            result["diff"] = outcome.diff.to_dict()
            result["rebuilds"] = outcome.diff.rebuild_sentence()
        return result

    def _read_design(self) -> dict[str, Any]:
        from app.core import designs
        from app.models.design import DesignRevision

        conversation = self._design_conversation()
        document = designs.load(self.db, conversation)
        if document is None:
            return {
                "design": None,
                "note": (
                    "No design has been recorded for this conversation. Call "
                    "record_design once you know the shape of the part."
                ),
            }
        history = list(
            self.db.scalars(
                select(DesignRevision)
                .where(DesignRevision.design_id == document.id)
                .order_by(DesignRevision.revision_number.desc())
                .limit(HISTORY_PAGE_LIMIT)
            )
        )
        return {
            "design": document.document,
            "digest": document.digest,
            "revision": document.revision_number,
            "history": [
                {
                    "revision": row.revision_number,
                    "summary": row.summary,
                    "author": row.author,
                    "at": row.created_at.isoformat(),
                }
                for row in history
            ],
        }

    def _set_design_parameter(self, name: str, value: float) -> dict[str, Any]:
        from app.core import designs
        from app.design.errors import SpecError

        conversation = self._design_conversation()
        document = designs.load(self.db, conversation)
        if document is None:
            raise ToolError(
                "There is no design to edit. Call record_design first — a parameter "
                "can only be changed in a design that has been written down."
            )
        try:
            outcome = designs.set_parameter(
                self.db, document, name, float(value), author=designs.AUTHOR_AGENT, author_id=None
            )
        except SpecError as exc:
            # Through as-is: the message names the parameters that do exist, or
            # the formula a derived one is derived from. Rewriting it would make
            # it less useful to the one reader it has.
            raise ToolError(str(exc)) from None

        self.db.flush()
        result: dict[str, Any] = {
            "design": outcome.document.name,
            "parameter": name,
            "value": value,
            "revision": outcome.document.revision_number,
            "digest": outcome.document.digest,
        }
        if outcome.diff is not None:
            result["rebuilds"] = outcome.diff.rebuild_sentence()
            result["affected"] = list(outcome.diff.affected)
            result["downstream"] = list(outcome.diff.downstream)
            if not outcome.diff.plan_changed:
                result["note"] = (
                    "Nothing that affects the built part changed — no feature reads "
                    "this parameter."
                )
        return result

    # -- the plan, its checkpoints, and what a run costs (E16 tasks 2, 5, 6) --

    def _plan_work(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        """Store the declared plan, replacing whatever was there.

        Whole-graph, for the same reason `record_design` is whole-document: a
        merge needs a rule for "the model omitted a task" and both readings are
        defensible, so whichever was chosen would be wrong half the time and
        silently. `update_task` is the incremental path, and it moves a task
        rather than redefining one.

        Every refusal — a cycle, a dependency on nothing, a plan longer than the
        ceiling — arrives as one `PlanError` naming what to change, and it is
        passed through as-is.
        """
        from app.ai.taskgraph import PlanError, graph_from_tasks

        conversation = self._design_conversation()
        try:
            graph = graph_from_tasks(tasks or [])
        except PlanError as exc:
            raise ToolError(str(exc)) from None

        conversation.task_graph = graph.to_dict()
        self.db.flush()
        return {
            "tasks": len(graph),
            "order": [task.id for task in graph.order()],
            "ready": [task.id for task in graph.ready()],
            "checkpoints": [task.id for task in graph.checkpoints()],
            "plan": graph.brief(),
        }

    def _update_task(self, id: str, state: str, note: str = "") -> dict[str, Any]:
        from app.ai.taskgraph import PlanError, TaskGraph, TaskState

        conversation = self._design_conversation()
        try:
            graph = TaskGraph.from_dict(conversation.task_graph)
        except PlanError as exc:  # pragma: no cover - a stored plan this build cannot read
            raise ToolError(str(exc)) from None
        if not graph:
            raise ToolError(
                "There is no plan to update. Call plan_work first — a step can only be "
                "moved in a plan that was written down."
            )
        try:
            moved = graph.advance(id, TaskState(state), note=note)
        except ValueError as exc:
            # `PlanError` for an out-of-order move (its message names the
            # dependencies), plain `ValueError` for a state this build does not
            # know. Both are answers the model can act on.
            raise ToolError(str(exc)) from None

        conversation.task_graph = moved.to_dict()
        self.db.flush()
        result: dict[str, Any] = {
            "task": id,
            "state": state,
            "ready": [task.id for task in moved.ready()],
            "settled": f"{moved.settled_count()} of {len(moved)}",
        }
        open_checkpoints = [task.id for task in moved.open_checkpoints()]
        if open_checkpoints:
            result["awaiting_sign_off"] = open_checkpoints
        if moved.complete:
            result["note"] = "Every step of the plan is settled."
        elif not moved.ready():
            # The useful answer rather than an empty list. A model handed `[]`
            # reads it as "nothing to do" and closes the turn reporting success.
            result["note"] = (
                "Nothing is ready: everything left is blocked or waiting on a sign-off. "
                "Say what is needed rather than retrying."
            )
        return result

    def _request_approval(
        self, title: str, question: str, task_id: str | None = None
    ) -> dict[str, Any]:
        """Raise a gate and end the turn on it (E16 task 5).

        **The gate is pinned to the design as it stands**, which is what makes
        the eventual approval mean something. `core/gates.raise_gate` takes the
        digest at this moment and `decide` re-digests what the decider is looking
        at, so an approval cannot land on a design that moved while it was
        pending. If there is no design yet the subject is the plan, which is
        equally pinnable and equally worth signing off.

        Ending the turn is the point rather than a side effect. A checkpoint the
        agent announces and then walks past is not a checkpoint; the loop reads
        `awaiting_approval` in the result and stops.
        """
        from app.ai.taskgraph import TaskGraph
        from app.core import designs, gates

        conversation = self._design_conversation()
        organisation_id = self._organisation_id()
        if organisation_id is None:
            raise ToolError(
                "This account has no organisation, so there is nobody to ask. Carry on "
                "and say in your answer what you would have wanted signed off."
            )

        document = designs.load(self.db, conversation)
        graph = TaskGraph.from_dict(conversation.task_graph)
        if document is not None:
            subject_type, subject_id = "design", document.id
            subject: Any = document.document
            evidence: dict[str, Any] = {
                "design": document.name,
                "revision": document.revision_number,
                "digest": document.digest,
            }
        else:
            subject_type, subject_id = "plan", conversation.id
            subject = graph.to_dict()
            evidence = {"plan": graph.brief()}
        if task_id:
            evidence["task"] = task_id

        gate = gates.raise_gate(
            self.db,
            organisation_id=organisation_id,
            requested_by=self.user,
            title=title[:255],
            question=question[:4000],
            subject_type=subject_type,
            subject_id=subject_id,
            subject=subject,
            evidence=evidence,
            project_id=conversation.project_id,
            conversation_id=conversation.id,
        )
        self.db.flush()
        return {
            # Read by the agent loop, which ends the turn on it. A key rather
            # than an exception because the call *succeeded* — a gate exists —
            # and raising would file a working checkpoint as a failure.
            "awaiting_approval": True,
            "gate_id": gate.id,
            "title": gate.title,
            "pinned_to": subject_type,
            "next_step": (
                "Stop here and tell the user what you are asking them to decide and why. "
                "Nothing is lost while it is open; the work resumes when they answer."
            ),
        }

    def _estimate_cost(self) -> dict[str, Any]:
        """What a run is likely to cost this tenant (E16 task 6).

        Reads P8's estimator rather than computing anything: the number has to
        come from the meter that bills, or the estimate and the invoice are two
        answers to one question. Each `Estimate` already carries its own
        sentence, including the one that says there is not enough history — which
        is passed through rather than turned into a zero.
        """
        from datetime import date

        from app.core.estimates import estimate_run

        organisation_id = self._organisation_id()
        if organisation_id is None:
            return {
                "estimates": [],
                "note": (
                    "This account has no organisation, so there is no billing history "
                    "to estimate from. Say so rather than guessing at a cost."
                ),
            }
        estimates = estimate_run(self.db, organisation_id, today=date.today())
        return {
            "estimates": [
                {
                    "meter": estimate.meter.value,
                    "sentence": estimate.human(),
                    "known": estimate.known,
                    "samples": estimate.samples,
                }
                for estimate in estimates
            ],
            "next_step": (
                "Report these sentences as written. An estimate we do not have is not "
                "a small one."
            ),
        }

    def _organisation_id(self) -> str | None:
        from app.models.organisation import organisation_ids_for_user

        tenants = organisation_ids_for_user(self.db, self.user)
        # Sorted so a user in two organisations gets a stable answer rather than
        # whichever the set happened to yield first — a gate raised against a
        # different tenant on each call would be unfindable.
        return next(iter(sorted(tenants)), None)

    def _list_projects(self) -> dict[str, Any]:
        rows = self.db.scalars(
            select(Project)
            .where(Project.owner_id == self.user.id)
            .order_by(Project.created_at.desc())
        ).all()
        return {
            "projects": [{"id": p.id, "name": p.name, "description": p.description} for p in rows],
            "current_project_id": self.project_id,
        }

    def _get_project(self, project_id: str | None = None) -> dict[str, Any]:
        """One round trip for "what is in this project".

        Counts come from dedicated aggregate queries rather than loading the
        collections and taking `len()` -- a project with 400 simulations should
        not materialise 400 rows to answer "how many".
        """
        project = self._project(project_id)
        geometry_count = self.db.scalar(
            select(func.count())
            .select_from(GeometryVersion)
            .where(GeometryVersion.project_id == project.id)
        )
        simulation_count = self.db.scalar(
            select(func.count())
            .select_from(SimulationJob)
            .where(SimulationJob.project_id == project.id)
        )
        latest = self.db.scalar(
            select(GeometryVersion)
            .where(GeometryVersion.project_id == project.id)
            .order_by(GeometryVersion.version_number.desc())
            .limit(1)
        )
        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "geometry_version_count": int(geometry_count or 0),
            "simulation_count": int(simulation_count or 0),
            "latest_geometry": (
                {
                    "version": latest.version_number,
                    "filename": latest.filename,
                    "stats": latest.stats,
                }
                if latest is not None
                else None
            ),
        }

    def _update_project(
        self,
        project_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Rename or re-describe a project.

        Only the fields actually supplied are touched, so a caller changing the
        name cannot accidentally blank the description by omitting it.
        """
        project = self._project(project_id)
        if name is None and description is None:
            raise ToolError("Nothing to change: pass a new name, a new description, or both.")

        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise ToolError("A project name cannot be blank.")
            if len(cleaned) > 255:
                raise ToolError("That name is too long; keep it under 255 characters.")
            project.name = cleaned
        if description is not None:
            # An explicit empty string is how the model clears a description;
            # `None` above already means "leave it alone".
            project.description = description.strip() or None

        self.db.commit()
        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "updated": [
                field
                for field, value in (("name", name), ("description", description))
                if value is not None
            ],
        }

    def _delete_project(self, project_id: str | None = None) -> dict[str, Any]:
        """Delete a project and everything under it.

        Mirrors the HTTP route's ordering: count what is about to go first, so
        the reply can tell the user exactly what was destroyed, then let the
        cascade run. Gated behind `mutating`, so it cannot fire without the
        caller having confirmed this turn.
        """
        project = self._project(project_id)
        geometry_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(GeometryVersion)
                .where(GeometryVersion.project_id == project.id)
            )
            or 0
        )
        simulation_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(SimulationJob)
                .where(SimulationJob.project_id == project.id)
            )
            or 0
        )
        running = self.db.scalar(
            select(func.count())
            .select_from(SimulationJob)
            .where(
                SimulationJob.project_id == project.id,
                SimulationJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
        )
        if running:
            # Deleting the row out from under a live worker leaves it writing
            # results to a project that no longer exists.
            raise ToolError(
                f"{running} simulation(s) in this project are still queued or running. "
                "Wait for them to finish, or delete them first."
            )

        deleted = {
            "id": project.id,
            "name": project.name,
            "geometry_versions_deleted": geometry_count,
            "simulations_deleted": simulation_count,
        }
        self.db.delete(project)
        self.db.commit()
        if self.project_id == deleted["id"]:
            # The conversation's scope just stopped existing; drop it rather
            # than leaving every later tool call resolving a dead id.
            self.project_id = None
        return {
            **deleted,
            "next_step": (
                "Tell the user exactly what was deleted, including the counts. "
                "This cannot be undone."
            ),
        }

    def _list_materials(self) -> dict[str, Any]:
        return {
            "materials": [m.model_dump() for m in MATERIALS.values()],
            "note": "Use one of these names exactly. Do not invent property values.",
        }

    def _list_geometry(self, project_id: str | None = None) -> dict[str, Any]:
        project = self._project(project_id)
        rows = self.db.scalars(
            select(GeometryVersion)
            .where(GeometryVersion.project_id == project.id)
            .order_by(GeometryVersion.version_number.desc())
        ).all()
        if not rows:
            raise ToolError(
                f"Project {project.name!r} has no geometry yet. Either the user uploads a "
                "STEP, IGES or STL file, or you build the part and export it with "
                "catia_export_step — which works on the open kernel as well as on a seat."
            )
        return {
            "project_id": project.id,
            "geometry_versions": [
                {
                    "version_number": g.version_number,
                    "filename": g.filename,
                    "file_format": g.file_format,
                    "bounding_box_mm": (g.stats or {}).get("bounding_box"),
                }
                for g in rows
            ],
            "supported_formats": sorted(ext for exts in GEOMETRY_FORMATS.values() for ext in exts),
        }

    def _list_simulations(self, project_id: str | None = None, limit: int = 10) -> dict[str, Any]:
        project = self._project(project_id)
        rows = self.db.scalars(
            select(SimulationJob)
            .where(SimulationJob.project_id == project.id)
            .order_by(SimulationJob.created_at.desc())
            .limit(max(1, min(limit, 50)))
        ).all()
        return {
            "project_id": project.id,
            "simulations": [
                {
                    "id": s.id,
                    "status": s.status.value,
                    "load_case_name": (s.load_case or {}).get("name"),
                    "factor_of_safety": (s.result or {}).get("factor_of_safety"),
                    "max_von_mises_mpa": (s.result or {}).get("max_von_mises_mpa"),
                    "error": s.error,
                }
                for s in rows
            ],
        }

    def _simulation(self, simulation_id: str) -> SimulationJob:
        job = self.db.get(SimulationJob, simulation_id)
        if job is None:
            raise ToolError(f"No simulation with id {simulation_id!r}.")
        self._project(job.project_id)  # ownership check, raises if not theirs
        return job

    def _get_simulation(self, simulation_id: str) -> dict[str, Any]:
        job = self._simulation(simulation_id)
        return {
            "id": job.id,
            "status": job.status.value,
            "load_case": job.load_case,
            "element_size_mm": job.element_size_mm,
            "mesh_stats": job.mesh_stats,
            "result": job.result,
            "error": job.error,
        }

    def _draft_load_case(
        self,
        description: str,
        project_id: str | None = None,
        geometry_version: int | None = None,
    ) -> dict[str, Any]:
        """A load case from a sentence, against this project's real geometry.

        The drafting itself has existed since the load-case route was written
        and was reachable only from the web form. Measured on ladder prompt H4,
        2026-09-06: the agent built the bracket correctly, then tried four
        times to hand-write a `LoadCase` for it, failed pydantic validation
        every time, and the run the prompt was actually asking for never
        happened. The capability was in the product and not in the tool set.

        Returned as a *draft*, with its assumptions and its unresolved
        questions attached and no run started. That is the contract the route
        already had, and it is the honest one: the numbers in here are choices,
        and the user has to see which ones were theirs.
        """
        from app.ai.service import draft_load_case as draft

        project = self._project(project_id)
        if self.provider is None:
            raise ToolError(
                "No model is available to draft a load case here. Write the load "
                "case out and pass it to run_simulation."
            )

        stmt = select(GeometryVersion).where(GeometryVersion.project_id == project.id)
        if geometry_version is None:
            stmt = stmt.order_by(GeometryVersion.version_number.desc())
        else:
            stmt = stmt.where(GeometryVersion.version_number == geometry_version)
        version = self.db.scalars(stmt).first()
        if version is None:
            raise ToolError(
                "This project has no geometry to resolve a load case against. Export "
                "the part first with catia_export_step, then call this again."
            )

        box = (version.stats or {}).get("bounding_box")
        if not box:
            raise ToolError(
                f"Geometry version {version.version_number} has no bounding box, so "
                "'the top face' and 'the far end' cannot be resolved. Re-export the "
                "part and try again."
            )

        completion = draft(self.provider, description=description, bounding_box=box)
        draft_result = completion.value
        return {
            "load_case": draft_result.load_case.model_dump(mode="json"),
            "assumptions": draft_result.assumptions,
            "unresolved": draft_result.unresolved,
            "geometry_version": version.version_number,
            "note": (
                "A draft. Show the assumptions to the user before running it, and "
                "say which numbers you chose. Pass load_case straight to "
                "run_simulation when they are happy."
            ),
        }

    def _run_simulation(
        self,
        load_case: dict[str, Any],
        project_id: str | None = None,
        geometry_version: int | None = None,
        element_size_mm: float | None = None,
        element_order: int = 2,
    ) -> dict[str, Any]:
        """Queue a real mesh-and-solve run, exactly as the HTTP route does.

        **The default is quadratic, changed from linear on 2026-09-11.** See
        `app/schemas/simulation.py` for the measurement; the short version is
        that linear tets got a cantilever's tip deflection wrong by 3.6x and
        scattered its peak stress by 2.8x across three identical runs, and this
        is the entry point the agent actually uses.
        """
        project = self._project(project_id)

        if element_order not in (1, 2):
            raise ToolError(
                f"element_order must be 1 (linear tets) or 2 (quadratic); got {element_order!r}."
            )

        if self.job_queue is None or self.session_scope is None or self.media_store is None:
            # A configuration fault, not a model mistake -- but it still reaches
            # the model as a tool error, because the alternative is a 500 that
            # loses the turn and the transcript with it.
            raise ToolError(
                "Simulations cannot be submitted from this context. Tell the user to "
                "start the run from the project page, and do not claim it is running."
            )

        # Validate before touching the queue: a Pydantic failure here becomes a
        # tool error the model can read and correct, rather than a 500 later.
        try:
            validated = LoadCase.model_validate(load_case)
        except ValidationError as exc:
            raise ToolError(_load_case_problem(exc)) from exc
        except Exception as exc:
            raise ToolError(
                f"That load case is not valid: {exc}. Fix it and call run_simulation again."
            ) from exc

        stmt = select(GeometryVersion).where(GeometryVersion.project_id == project.id)
        if geometry_version is None:
            stmt = stmt.order_by(GeometryVersion.version_number.desc())
        else:
            stmt = stmt.where(GeometryVersion.version_number == geometry_version)
        version = self.db.scalars(stmt).first()
        if version is None:
            raise ToolError("No matching geometry version. Call list_geometry to see what exists.")

        # Before the queue, not in the worker. The runner makes the same check,
        # but a refusal from there arrives as a *failed job* -- which the agent
        # discovers only by polling, and answers by resubmitting. Ladder prompt
        # H4 run 8 (2026-09-06) spent three of its twenty rounds on exactly
        # that. Here it is one round, and the message carries the size that
        # would have fitted.
        try:
            check_mesh_request(version.stats, element_size_mm)
        except MeshError as exc:
            raise ToolError(str(exc)) from exc

        # Refuse a duplicate rather than silently burning compute on a run the
        # user already has -- the agent cannot see cost, so the tool enforces it.
        running = self.db.scalar(
            select(func.count())
            .select_from(SimulationJob)
            .where(
                SimulationJob.project_id == project.id,
                SimulationJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
        )
        if running:
            raise ToolError(
                f"{running} simulation(s) are already queued or running in this project. "
                "Wait for them to finish before submitting another."
            )
        self._assert_within_quota()

        job = SimulationJob(
            project_id=project.id,
            geometry_version_id=version.id,
            status=JobStatus.QUEUED,
            solver=LinearStaticSolver.name,
            load_case=validated.model_dump(),
            element_size_mm=element_size_mm,
            element_order=element_order,
        )
        self.db.add(job)
        # Commit before submitting: the worker looks the job up by id in its own
        # session and would find nothing inside our open transaction.
        self.db.commit()

        job_id = job.id
        scope = self.session_scope
        store = self.media_store
        self.job_queue.submit(lambda: run_simulation(job_id, scope, store))
        self.db.refresh(job)

        return {
            "id": job.id,
            "status": job.status.value,
            "project_id": project.id,
            "geometry_version_number": version.version_number,
            "load_case_name": validated.name,
            "element_size_mm": element_size_mm,
            "element_order": element_order,
            "note": (
                "Queued. Meshing and solving take minutes; call get_simulation with "
                "this id to find out how it went. Do not report a result yet."
            ),
        }

    def _assert_within_quota(self) -> None:
        """Refuse a run when the user already holds their share of the workers.

        The same ceiling the HTTP route applies (`_assert_within_quota` in
        `api/routes/simulations.py`). Checked here as well because the two paths
        submit to the same shared queue, and the agent is the path that can
        submit repeatedly without a human clicking anything.
        """
        limit = settings.max_concurrent_simulations_per_user
        running = (
            self.db.scalar(
                select(func.count())
                .select_from(SimulationJob)
                .join(Project, Project.id == SimulationJob.project_id)
                .where(
                    Project.owner_id == self.user.id,
                    SimulationJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
            or 0
        )
        if running >= limit:
            raise ToolError(
                f"You already have {running} simulation(s) queued or running, which is "
                f"the limit of {limit}. Wait for one to finish before submitting another."
            )

    def _delete_simulation(self, simulation_id: str) -> dict[str, Any]:
        """Delete one finished run, mirroring the HTTP route's ordering."""
        job = self._simulation(simulation_id)
        if not job.status.is_terminal:
            raise ToolError(
                f"Simulation {job.id} is {job.status.value}; wait for it to finish "
                "before deleting it."
            )
        if job.fields_media is not None and self.media is None:
            # Dropping the row without the blob would leak a file nothing
            # references. Refuse rather than half-delete.
            raise ToolError(
                "Result fields cannot be deleted from this context. Tell the user to "
                "delete the run from the project page."
            )

        fields = job.fields_media
        deleted = {
            "id": job.id,
            "status": job.status.value,
            "factor_of_safety": (job.result or {}).get("factor_of_safety"),
        }
        self.db.delete(job)
        self.db.flush()
        if fields is not None and self.media is not None:
            self.media.delete(fields)
        self.db.commit()
        return {"deleted": deleted, "note": "This run is gone and cannot be recovered."}

    # -- CATIA --------------------------------------------------------------

    def _build_knowledge(self) -> list[Tool]:
        """The documentation lookup, or nothing when there is no material to look in.

        Gated on the index actually existing rather than merely being enabled.
        A tool that is always in the model's vocabulary and always returns
        nothing is worse than an absent one: the model calls it, reads an empty
        result, and either calls it again with a reworded query or tells the
        user their documentation is empty. Neither is true, and both cost a step.
        """
        if not settings.knowledge_enabled:
            return []
        service = knowledge_service()
        if not service.available:
            return []

        return [
            Tool(
                name="search_documentation",
                description=(
                    "Search the CATIA and FEA reference manuals available on this "
                    "machine and get back the most relevant passages, each with the "
                    "document and page it came from. Use it whenever the answer "
                    "depends on how CATIA actually behaves -- which workbench a "
                    "command lives in, what a dialog field means, what a feature "
                    "requires before it can be created, how an analysis case is set "
                    "up -- rather than answering from memory. Search with the "
                    "technical terms themselves ('edge fillet radius', 'depouille "
                    "angle', 'shell thickness'); the manuals are in English and "
                    "French and either works. Cite the document and page when you "
                    "use what it returns."
                ),
                parameters=_object(
                    {
                        "query": {
                            "type": "string",
                            "description": (
                                "What to look up, in the vocabulary the manuals use. "
                                "A few precise terms beat a full sentence."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "How many passages to return. Defaults to "
                                f"{settings.knowledge_max_passages}; raise it only for "
                                "a genuinely broad question."
                            ),
                        },
                        "language": {
                            "type": "string",
                            "description": (
                                "Two-letter code for the language to prefer, when you "
                                "know it: the language CATIA's interface is running in, "
                                "or the one the user is writing to you in. 'fr' or 'en'. "
                                "It only reorders results -- the other language is still "
                                "returned when it is the better match -- so pass it "
                                "whenever you have it, and leave it out when you do not."
                            ),
                        },
                    },
                    required=["query"],
                ),
                handler=self._search_documentation,
            )
        ]

    def _search_documentation(
        self, query: str, limit: int | None = None, language: str | None = None
    ) -> dict[str, Any]:
        """Look `query` up in the reference corpus.

        Never raises on a retrieval failure -- `KnowledgeService.search` already
        guarantees that -- so the only error this can produce is a rejected
        argument, which the model can correct.

        An empty result is returned as an explicit, self-describing payload
        rather than a bare empty list. A model handed `{"passages": []}` tends to
        report that the documentation is missing; told in words that the manuals
        were searched and had nothing on this term, it moves on and answers from
        what it knows, which is the behaviour wanted.
        """
        if not isinstance(query, str) or not query.strip():
            raise ToolError("search_documentation needs a non-empty query.")

        ceiling = max(1, min(int(limit or settings.knowledge_max_passages), 10))
        passages = knowledge_service().search(query, limit=ceiling, language=language)
        if not passages:
            return {
                "query": query,
                "passages": [],
                "note": (
                    "The reference manuals contain nothing matching those terms. "
                    "Answer from your own knowledge, or try the term the manuals "
                    "would use instead."
                ),
            }
        return {
            "query": query,
            "passages": [
                {
                    "source": passage.source,
                    "page": passage.page,
                    "section": passage.heading,
                    "language": passage.language,
                    "text": passage.text,
                }
                for passage in passages
            ],
        }

    # -- the structured CATIA reference -------------------------------------

    def _build_catia_reference(self) -> list[Tool]:
        """The structured CATIA lookup. Always present when the data is.

        Distinct from `search_documentation` on purpose, and the difference is
        worth the second tool: that one returns manual *prose*, which is deep
        but has to be read and interpreted; this one returns *fields* -- the
        workbench, the exact menu path, the dialog options, the licence tier,
        the known failure modes, the localised name. A model asked "which
        workbench is Joggle in" gets an answer it can state verbatim rather than
        a page it has to summarise, and it cannot hallucinate a menu path it was
        handed.
        """
        service = catia_knowledge()
        if not service.available:
            return []

        return [
            Tool(
                name="explain_catia_term",
                description=(
                    "Look up any CATIA V5 term in the structured reference and get "
                    "back exactly what it is: which workbench and toolbar it lives "
                    "in, the full menu path, the dialog fields and their options, "
                    "what must exist before it can run, the licence tier it needs, "
                    "how it typically fails and what to do about that, and the "
                    "command's name in other interface languages. Works on command "
                    "names, workbench names, product codes (PDG, ASL, GSD), file "
                    "formats, Tools>Options settings, error message text, and "
                    "aerospace vocabulary (joggle, stringer, STA/BL/WL, ply drop-off). "
                    "Understands misnames, abbreviations and the French, German, "
                    "Italian and Spanish interface names, so pass the user's own "
                    "words. Call this BEFORE stating any menu path, toolbar or "
                    "workbench -- those are precisely the details that are "
                    "confidently wrong from memory."
                ),
                parameters=_object(
                    {
                        "term": {
                            "type": "string",
                            "description": (
                                "What to look up, in the user's own words. "
                                "'edge fillet', 'Kantenverrundung', 'joggle', "
                                "'ASL', 'the profile is open and not limited'."
                            ),
                        },
                        "language": {
                            "type": "string",
                            "description": (
                                "Two-letter code for the language the user's CATIA "
                                "interface is running in, when you know it: 'fr', "
                                "'de', 'it', 'es'. The reply then names the command "
                                "as their menus actually show it, or says plainly "
                                "that the translation is not recorded."
                            ),
                        },
                    },
                    required=["term"],
                ),
                handler=self._explain_catia_term,
            )
        ]

    def _explain_catia_term(self, term: str, language: str | None = None) -> dict[str, Any]:
        """Resolve `term` against the structured reference.

        An empty result is returned as a self-describing payload for the same
        reason `_search_documentation` does it: handed a bare empty list, a model
        reports that CATIA has no such command, which is a much stronger claim
        than "this reference does not carry it".
        """
        if not isinstance(term, str) or not term.strip():
            raise ToolError("explain_catia_term needs a non-empty term.")

        service = catia_knowledge()
        matches = service.lookup(term, language=language)
        payload: dict[str, Any] = {"term": term, "matches": matches}

        fork = service.disambiguation(term)
        if fork is not None:
            payload["ambiguous"] = fork

        if not matches:
            payload["note"] = (
                "The structured reference has no entry under that name. It may still "
                "be a real CATIA term -- try search_documentation, or the term the "
                "manuals would use. Do not tell the user the command does not exist."
            )
        return payload

    def _build_catia(self) -> list[Tool]:
        """One agent tool per bridge spec, or nothing when CATIA is unavailable."""
        dispatch = _catia_dispatch()
        if dispatch is None:
            return []
        # What the *connected* bridge can run, not the whole registry. A daemon
        # older than the server, or a mock, implements a subset; a model offered
        # a tool that comes back "not implemented by this bridge" has spent a
        # turn to learn nothing actionable.
        #
        # Resolved with an explicit `getattr` rather than a try/except around
        # the call. Wrapping the call would also swallow an AttributeError
        # raised *inside* it, and the symptom of that is an empty tool list --
        # the agent silently loses every CATIA capability and reports that no
        # such tool exists, which reads as a missing feature rather than a bug.
        offered = getattr(dispatch, "offered_tool_specs", None)
        user = getattr(self, "user", None)
        if offered is None:  # pragma: no cover - protocol not implemented yet
            logger.warning("app.catia.dispatch exposes no offered_tool_specs")
            specs = list(getattr(dispatch, "CATIA_TOOL_SPECS", []))
        elif user is None:
            # A toolbox built without a user — the introspection paths that
            # enumerate the vocabulary do this. There is no device to intersect
            # against, so the answer is the same as for a user with none
            # connected: offer the whole registry.
            specs = list(dispatch.CATIA_TOOL_SPECS)
        else:
            specs = list(offered(self.db, user.id))
        if not specs:
            return []

        tools: list[Tool] = []
        for spec in specs:
            tools.append(
                Tool(
                    name=spec.name,
                    description=spec.description,
                    parameters=spec.parameters,
                    handler=self._catia_handler(spec.name),
                    # The spec's own tier decides this. Hard-coding a list here
                    # would let a new destructive tool ship ungated the day the
                    # bridge added it.
                    mutating=bool(getattr(spec, "mutating", True)),
                )
            )
        return tools

    def _check_part(self, claims: list[dict[str, Any]]) -> dict[str, Any]:
        """Measure the built part and check the model's own claims against it.

        Master plan Decision 3 reaching the conversation. `assertions.py` has
        existed since 2026-09-04 and could not be called from a chat, so the only
        thing standing between "every tool returned ok" and "the part is right"
        was the model's opinion. Measured on this seat 2026-09-05: a flange whose
        every call succeeded came out with its bolt circle on a 99 mm diameter
        instead of 70 and every edge rounded instead of four, and read as a
        complete success in the transcript.

        **Nothing is re-implemented here.** The measurement comes from the same
        `catia_measure` both backends already answer, and the comparison is
        `check_assertions` unchanged — so `UNMEASURED` keeps its meaning, a
        formula bound keeps working, and provenance still marks an approximate
        number as approximate. This is a seam, not a second checker.
        """
        from app.design.assertions import Assertion, check_assertions

        if not claims:
            raise ToolError(
                "check_part needs at least one claim. State what the part is "
                "supposed to be -- a width, a mass, a solid count -- as numbers."
            )

        parsed: list[Assertion] = []
        for index, claim in enumerate(claims, start=1):
            try:
                parsed.append(Assertion.from_dict(claim))
            except Exception as bad:
                # The compiler's own message names the field and what is wrong
                # with it, which is better feedback than anything restated here.
                raise ToolError(f"Claim {index} is not usable: {bad}") from bad

        measurement = self._call_catia("catia_measure", {})
        if not isinstance(measurement, dict):
            raise ToolError(
                "The measurement came back in a shape check_part cannot read. "
                "Call catia_measure on its own and see what it says."
            )
        # The kernel route wraps its payload; the bridge returns it flat. Both are
        # legitimate and the caller should not have to know which backend answered.
        payload = measurement.get("measurements")
        if not isinstance(payload, dict):
            payload = measurement

        report = check_assertions(parsed, payload)
        out = report.to_dict()
        out["summary"] = report.summary()

        # A bounding box is unchanged by every internal feature. A claim set made
        # only of extents therefore passes on a part whose bore is missing, whose
        # holes are in the wrong place and whose fillets took the wrong edges —
        # which is exactly what happened here on 2026-09-05: width, height and
        # thickness all passed on a flange with the wrong bolt circle.
        #
        # Volume and mass are the two quantities that move when any feature does,
        # so a suite with neither has not checked the part, only its envelope.
        # Said as a caveat rather than a refusal: the caller may legitimately be
        # checking one dimension mid-build, and refusing that would teach the
        # model to stop calling this at all.
        watching = {a.measure.split("[")[0].split(".")[0] for a in parsed}
        if not watching & {"volume_mm3", "mass_kg"}:
            out["blind_to_features"] = (
                "None of these claims reads volume_mm3 or mass_kg, so none of them "
                "can see an internal feature. A bounding box is the same whether "
                "the bore was cut or not. Add a volume or mass claim before "
                "calling the part verified."
            )
        # Said in words as well as in the structure, because the whole point is
        # that a model reading this cannot mistake "not checked" for "checked".
        if report.unmeasured:
            out["warning"] = (
                f"{len(report.unmeasured)} claim(s) could not be measured. That is "
                "not a pass -- say which ones were not checked rather than "
                "reporting the part as verified."
            )
        return out

    def _catia_handler(self, name: str) -> Callable[..., Any]:
        def handler(**arguments: Any) -> Any:
            return self._call_catia(name, arguments)

        return handler

    def _bound_document(self) -> str | None:
        if self.conversation is None:
            return None
        return bound_document_name(self.db, self.conversation.id)

    def _call_catia(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run one bridge tool, enforcing the conversation-document binding first.

        The dispatcher resolves document names and paths itself -- the model
        never supplies either. What is enforced here is the *sequence*: a
        conversation owns at most one document, so the first geometry operation
        must be `catia_new_part` and a resumed conversation must reopen what it
        already owns. Checking before the call turns a slow, confusing failure
        on the workstation into an immediate, actionable tool error.
        """
        dispatch = _catia_dispatch()
        if dispatch is None:  # pragma: no cover - the tool would not exist
            raise ToolError("The CATIA bridge is not available in this deployment.")

        conversation = self.conversation
        bound = self._bound_document()

        if name == "catia_new_part" and bound:
            # `catia_open_document` is a seat operation -- it reopens a file from
            # disk -- and the open kernel has no disk copy to reopen: the
            # document is the live object already sitting in this process. It is
            # never offered to the model on `GEOMETRY_BACKEND=occt` (it is not in
            # `backends.local_tool_names()`), so telling the model to call it
            # anyway sent it looking for a tool that does not exist, getting
            # "there is no tool called...", and retrying `catia_new_part` forever
            # -- a dead end with no way out of the same conversation. Measured
            # live on 2026-09-05 driving the real chat endpoint against the
            # occt backend.
            if backends.is_local():
                # **The binding outlives the document it names.** The row is in
                # Postgres and survives anything; the open kernel's document is
                # live OCAF state in this process and does not survive a restart,
                # a worker recycle, or an LRU eviction. Refusing `catia_new_part`
                # on the strength of the row alone deadlocked the conversation
                # for good: every scoped tool answered "No document is open"
                # from the runner, and the one tool that could open one was
                # refused from the database. No way out without abandoning the
                # conversation -- and a plain `uvicorn` restart was enough to do
                # it. It also broke the eviction recovery the runner itself
                # advertises, which says in as many words to start again with
                # catia_new_part.
                #
                # So the guard asks the kernel, not the row. `_bind_document`
                # updates the existing row rather than inserting a second one,
                # so rebuilding rebinds cleanly.
                # A *session* is not a document. `session_for` builds a runner on
                # demand, so a scoped call that failed for any other reason
                # leaves an empty one behind -- and gating on the session alone
                # would call that empty runner a part and re-deadlock the
                # conversation. `dispatch._local_document` states the rule this
                # follows: on this backend the row is not the truth, the live
                # document is.
                live = backends.peek_session(conversation.id if conversation else None)
                if live is not None and getattr(live, "document", None) is not None:
                    # The third sentence was added on 2026-09-11 and it is the
                    # one this message was missing. Measured through the GUI:
                    # the model padded 200 mm instead of 10, diagnosed its own
                    # error correctly ("the current part has wrong thickness"),
                    # and then called `catia_new_part` three times looking for a
                    # way to start again. This refusal named two routes --
                    # continue building, or start a second part *for an
                    # assembly* -- and neither is "start over", so the model
                    # never recognised the escape hatch that was sitting in the
                    # second one. It did eventually try the assembly-component
                    # tool and was refused for an unrelated argument error, and
                    # the turn ended on an E16.4 escalation.
                    #
                    # (The tool is named once below and nowhere else in this
                    # file on purpose: `TestTheSecondPartRefusalIsBackendAccurate`
                    # counts the occurrences to prove the only mention sits
                    # inside this `backends.is_local()` guard, which is what
                    # makes naming it truthful. Do not add a second one, here or
                    # in a comment.)
                    #
                    # Naming the recovery costs nothing and is the difference
                    # between a wrong part the user has to notice and a wrong
                    # part the agent fixes. There is no `catia_delete_feature`
                    # on the open kernel (117 of 205 operations), so this really
                    # is the only route, which is exactly why it has to be said.
                    raise ToolError(
                        f"This conversation already owns the part {bound!r}, and it is "
                        "still open in memory -- there is nothing to reopen. Continue "
                        "building on it directly; call catia_list_features first if "
                        "you need to see what already exists. **If this part is wrong "
                        "and you want to start it again from nothing**, call "
                        "catia_assembly_component to close the one you have, then "
                        "catia_new_part for a fresh one -- that is also how you start a "
                        "SECOND part for an assembly: it records the open part as a "
                        "component and closes it, and then catia_new_part starts the "
                        "next one."
                    )
                # No live document: the row names something that is gone, so
                # building it again is the recovery, not a mistake to refuse.
            # A seat: allowed. Phase 14 made a conversation own a set of
            # documents with one active, so `catia_new_part` here starts a
            # second part and makes it current; `dispatch._bind_document`
            # deactivates the previous one rather than replacing it, and
            # the result names what is still owned. Refusing this was measured
            # on ladder prompt S2 to cost seven identical calls in one turn
            # and leave no route to an assembly at all.
        if name == "catia_open_document" and not bound:
            raise ToolError(
                "This conversation has no CATIA document yet, so there is nothing to "
                "open. Call catia_new_part to start one."
            )
        if name not in CATIA_NO_DOCUMENT_REQUIRED and not bound:
            # Same reason as the branch above: `catia_open_document` reopens a
            # file from disk, which only means something for a seat. A local
            # document with no DB row simply never existed -- there is nothing
            # for "resuming" to name -- so the only recovery offered here is
            # the one tool the open kernel actually has.
            if backends.is_local():
                raise ToolError(
                    f"No part is bound to this conversation, so {name} has nothing "
                    "to act on. Call catia_new_part to start one."
                )
            raise ToolError(
                f"No CATIA document is bound to this conversation, so {name} has "
                "nothing to act on. Call catia_new_part to start one, or "
                "catia_open_document if you are resuming."
            )

        spec = None
        try:
            spec = dispatch.get_spec(name)
        except Exception:  # noqa: BLE001 - helper is optional to us
            spec = None
        long_running = bool(getattr(spec, "long_running", False))
        timeout = settings.catia_export_timeout_s if long_running else settings.catia_call_timeout_s

        def run() -> Any:
            return dispatch.call_catia(
                self.db,
                user_id=self.user.id,
                conversation_id=conversation.id if conversation is not None else None,
                tool=name,
                arguments=arguments,
                timeout_s=timeout,
            )

        def as_tool_error(exc: Exception) -> ToolError:
            if isinstance(exc, dispatch.CatiaUnavailable):
                return ToolError(
                    f"No CATIA bridge is connected: {exc} Tell the user to start the "
                    "Kryova CATIA bridge on their Windows machine, and stop calling CATIA "
                    "tools until they say it is running."
                )
            return ToolError(f"CATIA refused {name}: {exc}")

        try:
            result = run()
        except (dispatch.CatiaUnavailable, dispatch.CatiaError) as exc:
            # CATIA not being open is not a refusal. It is the one failure this
            # product can fix by itself -- `open_in_catia` has always been able
            # to start CATIA -- and refusing instead told the model to ask the
            # *user* to go and start it, which is the single thing the tool
            # notes everywhere else in this file tell it never to do.
            #
            # Measured on the seat, run 11 of 2026-09-06: seven tool calls
            # ending in "CATIA is not running on this workstation. Start CATIA,
            # open or create a part", after which the model dutifully asked the
            # user to start CATIA and gave up on a part it was perfectly able to
            # build. The model had already recovered from its own earlier
            # mistakes by then and called `catia_new_part` correctly; this
            # refusal is what actually lost the run.
            #
            # So bring CATIA up and run the call again, once.
            if not self._start_catia_once():
                raise as_tool_error(exc) from exc
            try:
                result = run()
            except (dispatch.CatiaUnavailable, dispatch.CatiaError) as retry:
                raise as_tool_error(retry) from retry

        self._record_catia_state(result)
        return result

    def _start_catia_once(self) -> bool:
        """Start CATIA when a call failed only because it was not running.

        Returns True only when CATIA was genuinely absent and is now up, which
        is the one case where retrying the call can change the answer. Every
        other case is False on purpose:

        * the open kernel, which has no CATIA to start;
        * a CATIA that is *already* running -- the failure was then a real
          refusal, and retrying it would do nothing but repeat it;
        * a launch that did not work.

        Never raises: this runs inside the handler for somebody else's error,
        and an exception here would replace a precise, actionable message with
        whatever went wrong during recovery.
        """
        if backends.is_local():
            return False
        try:
            from app.catia.bridge import get_status
            from app.catia.bridge import launch as catia_launch

            if get_status().running:
                return False
            catia_launch(visible=True)
        except Exception:  # noqa: BLE001 - recovery must not mask the original failure
            return False
        # Bounded, and never raises. CATIA being up is what the daemon waits for.
        self._attach_local_bridge()
        return True

    def _record_catia_state(self, result: Any) -> None:
        """Cache the post-state the bridge reported, for the next turn's block.

        Every mutating tool returns rich post-state -- feature list, bounding
        box, mass -- and keeping the latest of it here is what lets the state
        block describe the part on a turn where no CATIA tool ran at all. The
        binding itself is not touched: `CatiaDocument` owns that.
        """
        conversation = self.conversation
        if conversation is None or not isinstance(result, dict):
            return

        updates = {key: result[key] for key in CATIA_STATE_KEYS if key in result}
        if not updates:
            return
        # Reassign rather than mutate in place: SQLAlchemy does not track
        # in-place edits to a JSONB dict, so the update would never persist.
        conversation.catia_state = {**(conversation.catia_state or {}), **updates}
        self.db.flush()

    # -- Direct COM CATIA bridge fallbacks -----------------------------------

    def _catia_status(self) -> dict[str, Any]:
        try:
            from app.catia.bridge import get_status

            status = get_status()
            return {
                "running": status.running,
                "version": status.version,
                "open_documents": status.document_count,
                "active_document": status.active_document,
                "detail": status.detail,
            }
        except Exception as exc:
            return {"running": False, "detail": str(exc)}

    def _open_in_catia(self, new_part: bool = True, name: str | None = None) -> dict[str, Any]:
        """Start CATIA and give the conversation exactly one document to build in.

        **One document, created by one COM client.** This used to launch CATIA
        and immediately create a part through the server's *own* direct-COM
        client (`app.catia.bridge.new_part`), which is a different COM client in
        a different OS process from the paired daemon every `catia_*` tool goes
        through. The model would then call `catia_new_part` for a document it
        could actually build into -- `open_in_catia`'s was bound to nothing --
        and CATIA ended up carrying two documents, one of them orphaned, with
        two unsynchronised COM clients driving one single-apartment application.
        Seat testing on 2026-09-06 measured what that costs: the daemon's first
        real call on the second document returned a raw `com_error` ("the RPC
        server is not available"), and it stayed broken on retry -- runs 8-10 in
        `docs/verification-2026-09-06/REPORT.md`, with a window screenshot
        showing both documents open at once.

        So when the daemon is up, the document is created *through it*, by the
        same `catia_new_part` path the model would have called itself: one
        document, one client, bound to the conversation. The direct-COM call
        survives only as the fallback for what it was written for -- a machine
        with nothing paired yet, where no `catia_*` tool would work at all.
        """
        from app.catia.bridge import CATIABridgeError
        from app.catia.bridge import launch as catia_launch
        from app.catia.bridge import new_part as catia_new_part_directly

        try:
            status = catia_launch(visible=True)
        except CATIABridgeError as exc:
            raise ToolError(str(exc)) from exc

        # CATIA is up, which is the one thing the bridge daemon was waiting for.
        # Attaching it here rather than on the next tool call means the model
        # gets to read "bridge: connected" in this very result, instead of
        # discovering it is still unavailable and concluding it has to ask the
        # user for help. It is bounded and never raises.
        bridge_ready = self._attach_local_bridge()

        created: str | None = None
        bound = False
        problem: str | None = None

        if new_part:
            existing = self._bound_document()
            if existing:
                # Re-entrant call. Creating anything here would abandon work the
                # conversation already owns -- and adding a second window is the
                # exact bug this method was rewritten to stop.
                created, bound = existing, True
            elif bridge_ready:
                try:
                    result = self._call_catia(
                        "catia_new_part", {"name": (name or "Part").strip() or "Part"}
                    )
                except ToolError as exc:
                    # CATIA *is* open, and saying so is worth more than failing
                    # the whole call: a bare refusal here reads to the model as
                    # "CATIA is not running", which is how three seat runs ended
                    # with the model asking the user to model the part by hand.
                    problem = str(exc)
                else:
                    created = str(result.get("doc_name") or name or "Part")
                    bound = True
            else:
                try:
                    document = catia_new_part_directly()
                except CATIABridgeError as exc:
                    raise ToolError(str(exc)) from exc
                created = document.name if document else None

        return {
            "running": True,
            "version": status.version,
            "created_document": created,
            "document_bound": bound,
            "bridge_connected": bridge_ready,
            **({"document_error": problem} if problem else {}),
            "note": self._open_in_catia_note(
                bridge_ready=bridge_ready, created=created, bound=bound, problem=problem
            ),
        }

    @staticmethod
    def _open_in_catia_note(
        *, bridge_ready: bool, created: str | None, bound: bool, problem: str | None
    ) -> str:
        """What the model should do next, given what actually happened."""
        if problem:
            return (
                "CATIA is open and the bridge is attached, but starting the part "
                f"failed: {problem} Call catia_new_part yourself to try again. Do "
                "not ask the user to model the part or to upload a file."
            )
        if bound and created:
            return (
                f"CATIA is open and the part {created!r} already belongs to this "
                "conversation -- do not call catia_new_part, it is already done. "
                "Build straight on it with the catia_* tools (catia_sketch_create "
                "next, usually). Do not ask the user to model it or to upload "
                "anything."
            )
        if bridge_ready:
            return (
                "CATIA is open and the Kryova bridge is attached to it. Build the "
                "part yourself with the catia_* tools -- do not ask the user to "
                "model it, and do not ask them to upload anything."
            )
        return (
            "CATIA is open. The bridge is still attaching; call the catia_* "
            "tool you need anyway, it waits for the connection. Do not ask "
            "the user to model the part or to upload a file."
        )

    def _attach_local_bridge(self) -> bool:
        """Wait, briefly, for this machine's bridge daemon to connect."""
        try:
            from app.catia.local_bridge import CONNECT_TIMEOUT_S, ensure_started

            return ensure_started(self.db, self.user.id, wait_s=CONNECT_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - a convenience must not fail the tool
            return False

    def _sync_geometry_from_catia(
        self, project_id: str | None = None, note: str | None = None
    ) -> dict[str, Any]:
        import tempfile
        from pathlib import Path

        from app.catia.bridge import CATIABridgeError, ExportFormat, export_active_document
        from app.geometry.inspect import GeometryError, inspect
        from app.media import MediaService, get_media_store
        from app.models import MediaKind

        project = self._project(project_id)

        with tempfile.TemporaryDirectory(prefix="kryova-catia-") as staging:
            try:
                exported = export_active_document(
                    Path(staging), ExportFormat.STEP, stem=f"project_{project.id[:8]}"
                )
            except CATIABridgeError as exc:
                raise ToolError(str(exc)) from exc

            media_service = MediaService(self.db, get_media_store())
            stored = media_service.store_path(
                owner_id=self.user.id,
                kind=MediaKind.CAD,
                path=exported,
                filename=exported.name,
                content_type="application/step",
                meta={"source": "catia", "catia_export_format": "stp"},
            )

            try:
                stats = inspect(media_service.local_path(stored), "step")
            except GeometryError as exc:
                media_service.delete(stored)
                self.db.commit()
                raise ToolError(f"CATIA exported a file Kryova could not read: {exc}") from exc

        version_number = (
            self.db.scalar(
                select(func.max(GeometryVersion.version_number)).where(
                    GeometryVersion.project_id == project.id
                )
            )
            or 0
        ) + 1

        version = GeometryVersion(
            project_id=project.id,
            media_id=stored.id,
            version_number=version_number,
            filename=stored.filename,
            file_format="step",
            note=(note or "Synced from CATIA").strip(),
            stats=stats,
        )
        self.db.add(version)
        self.db.commit()
        self.db.refresh(version)

        return {
            "geometry_version": version.version_number,
            "filename": version.filename,
            "size_bytes": stored.size_bytes,
            "stats": stats,
            "note": (
                "Geometry is in the project. You can now build a load case and "
                "call run_simulation against this version."
            ),
        }

    # -- dispatch -----------------------------------------------------------

    def labels(self) -> dict[str, str]:
        """Tool name -> human label, for the step list the UI renders."""
        return {name: tool_label(name) for name in self._tools}

    def every_tool(self) -> list[Tool]:
        """Every tool this box holds, offered or not.

        The retrieval selector scores against this rather than against the
        narrowed offer, which is the difference between choosing what to show and
        compounding a previous turn's choice.
        """
        return list(self._tools.values())

    def recent_tool_names(self, limit: int = 12) -> list[str]:
        """Tools this conversation has actually used, newest first.

        Continuity beats similarity for the retrieval selector (16.1): a model
        that called `catia_pattern_circular` last turn is likely to call it again,
        and a query that has moved on to "now measure it" would otherwise drop it
        out of the offer at exactly the wrong moment.

        Bounded and indexed — `(conversation_id, sequence)` is the transcript's
        own index, so this is the same lookup the window already does. Returns
        empty rather than raising if there is no conversation, because the
        introspection paths build a toolbox without one.
        """
        if self.conversation is None:
            return []
        rows = self.db.scalars(
            select(ConversationMessage.tool_name)
            .where(
                ConversationMessage.conversation_id == self.conversation.id,
                ConversationMessage.tool_name.is_not(None),
            )
            .order_by(ConversationMessage.sequence.desc())
            .limit(limit)
        ).all()
        seen: dict[str, None] = {}
        for name in rows:
            if name:
                seen.setdefault(name, None)
        return list(seen)

    def recent_user_messages(self, limit: int = 4) -> str:
        """The last few things the *user* said, newest first, as one blob.

        Fed to the tool selector (16.1) so a thin turn keeps the vocabulary the
        thick turn established: an engineer who said "four M8 holes on a bolt
        circle" two turns ago and now says "go on" must not lose the hole tools
        because this message carries no nouns.

        **User turns only.** Scoring the assistant's own replies would let the
        model widen its own offer by talking about tools, which is a loop with
        no floor — and the assistant's text is the one part of the transcript
        that is not evidence of what the engineer wants.

        Bounded and indexed on `(conversation_id, sequence)`, the same lookup
        the context window already does. Empty rather than raising when there is
        no conversation, because the introspection paths build a toolbox
        without one.
        """
        if self.conversation is None:
            return ""
        rows = self.db.scalars(
            select(ConversationMessage.content)
            .where(
                ConversationMessage.conversation_id == self.conversation.id,
                ConversationMessage.role == MessageRole.USER,
            )
            .order_by(ConversationMessage.sequence.desc())
            .limit(limit)
        ).all()
        return " ".join(text for text in rows if text)

    def schemas(
        self, include_mutating: bool, *, only: Collection[str] | None = None
    ) -> list[dict[str, Any]]:
        """The tool definitions sent to the model.

        `only` narrows what is *shown* — master plan 16.1, where 108 schemas are
        ~16k prompt tokens re-evaluated every turn and measurably cost the model
        its accuracy. It deliberately does **not** narrow what `call` accepts: a
        model that names a tool it was not offered still gets it. Retrieval is an
        attention optimisation, and the moment it starts refusing real tools it
        has become a capability cut wearing an optimisation's clothes.

        A name in `only` that is not a tool is ignored rather than refused — the
        selector works from specs and the toolbox from handlers, and the two can
        legitimately differ by a tool whose bridge went offline mid-turn.
        """
        return [
            tool.schema()
            for name, tool in self._tools.items()
            if (include_mutating or not tool.mutating) and (only is None or name in only)
        ]

    def is_mutating(self, name: str) -> bool:
        """Whether `name` changes something. Unknown names count as mutating.

        The safe default in both directions: an unknown name is refused by
        `call` anyway, and treating it as a read would let it slip past the
        repeat guard on its way there.
        """
        tool = self._tools.get(name)
        return True if tool is None else tool.mutating

    def call(self, name: str, arguments: dict[str, Any], *, allow_mutations: bool) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            # The nearest real name first, then the full list. With 26 tools the
            # bare alphabetical list buries the answer: a model that reached for
            # `catia_list_projects` gets eight `catia_*` names before
            # `list_projects`, and observed live it gave up rather than finding
            # it. A near-miss on the name is the common failure, so answer it
            # directly.
            close = difflib.get_close_matches(name, self._tools, n=3, cutoff=0.6)
            suggestion = f" Did you mean: {', '.join(close)}?" if close else ""
            raise ToolError(
                f"There is no tool called {name!r}.{suggestion} "
                f"Available: {', '.join(sorted(self._tools))}."
            )
        if tool.mutating and not allow_mutations:
            raise ToolError(
                f"{name} changes state and needs the user's confirmation first. "
                "Explain what you are about to do and ask them to confirm."
            )
        try:
            return tool.handler(**arguments)
        except ToolError:
            raise
        except TypeError as exc:
            # Wrong or missing argument names -- recoverable, so hand the model
            # the signature error instead of crashing the turn.
            raise ToolError(f"Bad arguments for {name}: {exc}") from exc
