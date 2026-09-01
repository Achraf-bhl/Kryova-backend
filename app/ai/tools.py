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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.state import bound_document_name
from app.catia_kb import catia_knowledge
from app.core.config import settings
from app.geometry.formats import GEOMETRY_FORMATS
from app.jobs import JobQueue
from app.media import LocalMediaStore, MediaService
from app.models import (
    Conversation,
    GeometryVersion,
    JobStatus,
    Project,
    SimulationJob,
    User,
)
from app.retrieval import knowledge_service
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
CATIA_NO_DOCUMENT_REQUIRED = frozenset({"catia_status", "catia_new_part", "catia_open_document"})

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
                                "1 for linear tets (the default), 2 for quadratic. "
                                "Quadratic elements are far more accurate in bending at "
                                "the same element count, at roughly 2.5x the degrees of "
                                "freedom and solve time. Use 2 when the part is loaded in "
                                "bending and the user cares about accuracy over turnaround."
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
                            "Start CATIA, bring its window to the screen, and optionally open a "
                            "fresh empty part for the user to model in. This is how a project "
                            "gets its geometry in this product: the user models in CATIA rather "
                            "than hunting for a file to upload. Call it right after creating a "
                            "project, once the user has said what they are building. Takes up to "
                            "a few minutes if CATIA is cold."
                        ),
                        parameters=_object(
                            {
                                "new_part": {
                                    "type": "boolean",
                                    "description": (
                                        "True to add a new empty CATPart. False to just bring up "
                                        "CATIA with whatever the user already has open."
                                    ),
                                }
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
                "STEP, IGES or STL file, or you build the part in CATIA and export it "
                "with catia_export_step."
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

    def _run_simulation(
        self,
        load_case: dict[str, Any],
        project_id: str | None = None,
        geometry_version: int | None = None,
        element_size_mm: float | None = None,
        element_order: int = 1,
    ) -> dict[str, Any]:
        """Queue a real mesh-and-solve run, exactly as the HTTP route does."""
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
        try:
            specs = list(dispatch.CATIA_TOOL_SPECS)
        except AttributeError:  # pragma: no cover - protocol not implemented yet
            logger.warning("app.catia.dispatch exposes no CATIA_TOOL_SPECS")
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
            raise ToolError(
                f"This conversation already owns the CATIA document {bound!r}. Call "
                "catia_open_document to work on it; creating a new part here would "
                "abandon everything already modelled."
            )
        if name == "catia_open_document" and not bound:
            raise ToolError(
                "This conversation has no CATIA document yet, so there is nothing to "
                "open. Call catia_new_part to start one."
            )
        if name not in CATIA_NO_DOCUMENT_REQUIRED and not bound:
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

        try:
            result = dispatch.call_catia(
                self.db,
                user_id=self.user.id,
                conversation_id=conversation.id if conversation is not None else None,
                tool=name,
                arguments=arguments,
                timeout_s=timeout,
            )
        except dispatch.CatiaUnavailable as exc:
            raise ToolError(
                f"No CATIA bridge is connected: {exc} Tell the user to start the "
                "Kryova CATIA bridge on their Windows machine, and stop calling CATIA "
                "tools until they say it is running."
            ) from exc
        except dispatch.CatiaError as exc:
            raise ToolError(f"CATIA refused {name}: {exc}") from exc

        self._record_catia_state(result)
        return result

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

    def _open_in_catia(self, new_part: bool = True) -> dict[str, Any]:
        from app.catia.bridge import CATIABridgeError
        from app.catia.bridge import launch as catia_launch
        from app.catia.bridge import new_part as catia_new_part

        try:
            status = catia_launch(visible=True)
            document = catia_new_part() if new_part else None
        except CATIABridgeError as exc:
            raise ToolError(str(exc)) from exc

        # CATIA is up, which is the one thing the bridge daemon was waiting for.
        # Attaching it here rather than on the next tool call means the model
        # gets to read "bridge: connected" in this very result, instead of
        # discovering it is still unavailable and concluding it has to ask the
        # user for help. It is bounded and never raises.
        bridge_ready = self._attach_local_bridge()

        return {
            "running": True,
            "version": status.version,
            "created_document": document.name if document else None,
            "bridge_connected": bridge_ready,
            "note": (
                "CATIA is open and the Kryova bridge is attached to it. Build the "
                "part yourself with the catia_* tools -- do not ask the user to "
                "model it, and do not ask them to upload anything."
                if bridge_ready
                else (
                    "CATIA is open. The bridge is still attaching; call the catia_* "
                    "tool you need anyway, it waits for the connection. Do not ask "
                    "the user to model the part or to upload a file."
                )
            ),
        }

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

    def schemas(self, include_mutating: bool) -> list[dict[str, Any]]:
        return [
            tool.schema() for tool in self._tools.values() if include_mutating or not tool.mutating
        ]

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
