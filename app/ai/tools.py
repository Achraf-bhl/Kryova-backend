"""The tools the agent may call, and the dispatcher that runs them.

Two rules shape this file.

**Every tool is scoped to one user.** A tool never takes an owner id from the
model -- the dispatcher is constructed with the authenticated user and filters
on it. A hallucinated project id therefore returns "not found", never someone
else's data, exactly like the HTTP layer.

**Mutating tools are marked.** `run_simulation` burns real compute and
`delete_simulation` destroys results, so both carry `mutating = True`. The
agent loop refuses to run those unless the caller passed `allow_mutations`,
which the API only sets when the user has confirmed. Read-only tools run
freely.

Tool descriptions are prompt text: the model reads them to decide what to call,
so they say *when* to use a tool, not just what it does.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.geometry.formats import GEOMETRY_FORMATS
from app.models import GeometryVersion, JobStatus, Project, SimulationJob, User
from app.solve.materials import MATERIALS
from app.solve.types import LoadCase


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


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


@dataclass
class ToolBox:
    """The tool set bound to one user and one database session."""

    db: Session
    user: User
    #: Set when the conversation is scoped to a project, so the model can say
    #: "the latest run" without repeating the id every turn.
    project_id: str | None = None
    _tools: dict[str, Tool] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        for tool in self._build():
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
        return [
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
                    {"project_id": {"type": "string", "description": "Omit to use the current project."}}
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
                    "result and any solver warnings. Call this before interpreting a "
                    "result or advising on a change."
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
                    "the user has asked for the run. Returns immediately with a job id -- "
                    "poll get_simulation for the outcome; do not assume it succeeded."
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
                name="catia_status",
                description=(
                    "Check whether CATIA is installed, running, and what is open in it. "
                    "Call this before offering anything CATIA-related, so you can tell "
                    "the user the truth about their setup rather than guessing. Never "
                    "fails -- on a machine without CATIA it simply reports running=false."
                ),
                parameters=_object({}),
                handler=self._catia_status,
            ),
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

    # -- handlers -----------------------------------------------------------

    def _create_project(self, name: str, description: str | None = None) -> dict[str, Any]:
        """Create a project and adopt it as the conversation's scope.

        Deliberately *not* marked mutating. That gate exists for tools that burn
        compute or destroy results (`run_simulation`); an empty project row is
        cheap and reversible, and gating it would mean a new-project chat has to
        ask permission for the thing the user just clicked a button to do. The
        expensive tools stay gated.
        """
        name = (name or "").strip()
        if not name:
            raise ToolError("A project needs a name. Ask the user what to call it.")
        # Mirrors ProjectCreate's bound rather than letting the DB truncate.
        if len(name) > 255:
            raise ToolError("That name is too long; keep it under 255 characters.")

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
                "Tell the user the project exists and ask them to upload a CAD file "
                "(STEP, IGES or STL). You cannot upload it for them."
            ),
        }

    def _list_projects(self) -> dict[str, Any]:
        rows = self.db.scalars(
            select(Project)
            .where(Project.owner_id == self.user.id)
            .order_by(Project.created_at.desc())
        ).all()
        return {
            "projects": [
                {"id": p.id, "name": p.name, "description": p.description} for p in rows
            ],
            "current_project_id": self.project_id,
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
                f"Project {project.name!r} has no geometry yet. The user must upload a "
                "STEP, IGES or STL file before anything can be analysed."
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
            "supported_formats": sorted(
                ext for exts in GEOMETRY_FORMATS.values() for ext in exts
            ),
        }

    def _list_simulations(
        self, project_id: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
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

    def _get_simulation(self, simulation_id: str) -> dict[str, Any]:
        job = self.db.get(SimulationJob, simulation_id)
        if job is None:
            raise ToolError(f"No simulation with id {simulation_id!r}.")
        self._project(job.project_id)  # ownership check, raises if not theirs
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
    ) -> dict[str, Any]:
        project = self._project(project_id)

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
            raise ToolError(
                "No matching geometry version. Call list_geometry to see what exists."
            )

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

        return {
            "ready_to_submit": True,
            "project_id": project.id,
            "geometry_version_number": version.version_number,
            "load_case": validated.model_dump(),
            "element_size_mm": element_size_mm,
            "note": (
                "Validated but NOT yet submitted -- the API layer submits it. "
                "Tell the user what will run and that results take minutes."
            ),
        }

    # -- CATIA --------------------------------------------------------------

    def _catia_status(self) -> dict[str, Any]:
        from app.catia import get_status

        status = get_status()
        return {
            "running": status.running,
            "version": status.version,
            "open_documents": status.document_count,
            "active_document": status.active_document,
            "detail": status.detail,
        }

    def _open_in_catia(self, new_part: bool = True) -> dict[str, Any]:
        from app.catia import CATIABridgeError
        from app.catia import launch as catia_launch
        from app.catia import new_part as catia_new_part

        try:
            status = catia_launch(visible=True)
            document = catia_new_part() if new_part else None
        except CATIABridgeError as exc:
            # The model should explain this to the user, not crash the turn.
            raise ToolError(str(exc)) from exc

        return {
            "running": True,
            "version": status.version,
            "created_document": document.name if document else None,
            "note": (
                "CATIA is open on the user's screen. Tell them to model the part "
                "there, then say when it is ready so you can call "
                "sync_geometry_from_catia."
            ),
        }

    def _sync_geometry_from_catia(
        self, project_id: str | None = None, note: str | None = None
    ) -> dict[str, Any]:
        """Export the active CATIA document straight into the project.

        This is the no-upload path: bytes go from CATIA to a temp file, into the
        content-addressed media store, and out as a GeometryVersion, without the
        user ever touching a file dialog.
        """
        import tempfile
        from pathlib import Path

        from app.catia import CATIABridgeError, ExportFormat, export_active_document
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
                raise ToolError(
                    f"CATIA exported a file Kryova could not read: {exc}"
                ) from exc

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

    def schemas(self, include_mutating: bool) -> list[dict[str, Any]]:
        return [
            tool.schema()
            for tool in self._tools.values()
            if include_mutating or not tool.mutating
        ]

    def call(self, name: str, arguments: dict[str, Any], *, allow_mutations: bool) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(
                f"There is no tool called {name!r}. Available: {', '.join(sorted(self._tools))}."
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
