"""The canonical model-state block.

The transcript is a record of what was *said*. It is not state, and treating it
as state is how an agent ends up confidently discussing a simulation that
failed, a geometry version that was replaced, or a CATIA document that was
closed an hour ago. Anything can change between turns: another browser tab
uploads a new STEP file, a queued job finishes, an admin deletes a run.

So the truth is rebuilt from the database on every single turn and injected
fresh. Nothing here is cached across turns and nothing is read out of the
conversation history. If the block and the transcript disagree, the block is
right -- and the system prompt tells the model so.

It is deliberately small. Every line has to earn its place in the context
window on every turn, so this carries the identifiers and statuses the agent
cannot function without, and leaves the detail to the tools that fetch it.
"""

import logging
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.ai.prompts import STATE_CLOSE, STATE_OPEN
from app.ai.sanitise import sanitise_untrusted
from app.core.config import settings
from app.models import (
    Conversation,
    GeometryVersion,
    JobStatus,
    Project,
    SimulationJob,
    User,
)

logger = logging.getLogger(__name__)

#: Cap on any single value copied out of the database into the block. Project
#: names and filenames are user- or file-supplied, so they get the same
#: untrusted treatment as a tool result -- shorter, because a state block is
#: meant to be scannable.
MAX_FIELD_CHARS = 200

_PREAMBLE = (
    "Live state, read from the database at the start of this turn. This is "
    "authoritative: where it disagrees with anything earlier in the "
    "conversation, it is right and the conversation is stale."
)


def _clean(value: Any) -> str:
    return sanitise_untrusted(str(value), max_chars=MAX_FIELD_CHARS)


def bound_document_name(db: Session, conversation_id: str | None) -> str | None:
    """The CATIA document this conversation owns, if any.

    `CatiaDocument.conversation_id` is the single source of truth for the
    binding -- it carries a unique constraint, and the bridge's own dispatcher
    writes it. The agent layer reads it and never keeps a second copy, because
    two answers to "which document is this" is exactly the bug that makes a
    resumed session open the wrong part.

    Guarded: the CATIA package is optional, and an agent serving a user with no
    workstation must not fail on its absence.
    """
    if not conversation_id:
        return None
    try:
        from app.models.catia import CatiaDocument
    except Exception:  # noqa: BLE001 - optional package
        return None
    from sqlalchemy import select as _select

    document = db.scalar(
        _select(CatiaDocument).where(CatiaDocument.conversation_id == conversation_id)
    )
    return document.doc_name if document is not None else None


def _catia_available(db: Session, user_id: str) -> bool | None:
    """Whether a CATIA bridge is connected, or None if the feature is absent.

    Guarded on both sides: the package may not be installed yet, and the bridge
    lookup itself touches a device registry that can fail independently of the
    conversation. Neither is a reason to fail the turn -- an agent that cannot
    tell whether CATIA is up is still useful for everything else.
    """
    if not settings.catia_enabled:
        return None
    try:
        from app.catia.dispatch import catia_available
    except Exception:  # noqa: BLE001 - the package is optional and in flight
        return None
    try:
        return bool(catia_available(db, user_id))
    except Exception:  # noqa: BLE001 - a bridge lookup must not kill the turn
        logger.warning("CATIA availability check failed", exc_info=True)
        return None


def _local_bridge_supported() -> bool:
    """Whether this server starts the CATIA daemon itself. Never raises."""
    try:
        from app.catia.local_bridge import is_supported
    except Exception:  # noqa: BLE001 - optional package
        return False
    try:
        return is_supported()
    except Exception:  # noqa: BLE001 - a state line must not kill the turn
        return False


def _project_lines(db: Session, project: Project) -> list[str]:
    lines = [f"project: {_clean(project.name)} (id {project.id})"]
    if project.description:
        lines.append(f"project_description: {_clean(project.description)}")

    versions = db.scalars(
        select(GeometryVersion)
        .where(GeometryVersion.project_id == project.id)
        .order_by(desc(GeometryVersion.version_number))
        .limit(1)
    ).all()
    total_versions = (
        db.scalar(
            select(func.count())
            .select_from(GeometryVersion)
            .where(GeometryVersion.project_id == project.id)
        )
        or 0
    )
    if not versions:
        lines.append("geometry: none uploaded yet")
    else:
        latest = versions[0]
        box = (latest.stats or {}).get("bounding_box")
        lines.append(
            f"geometry: {total_versions} version(s), latest v{latest.version_number} "
            f"{_clean(latest.filename)} ({latest.file_format})"
        )
        if box:
            lines.append(f"latest_bounding_box_mm: {box}")

    runs = db.scalars(
        select(SimulationJob)
        .where(SimulationJob.project_id == project.id)
        .order_by(desc(SimulationJob.created_at))
        .limit(1)
    ).all()
    active = (
        db.scalar(
            select(func.count())
            .select_from(SimulationJob)
            .where(
                SimulationJob.project_id == project.id,
                SimulationJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
        )
        or 0
    )
    if not runs:
        lines.append("latest_run: none")
    else:
        run = runs[0]
        summary = f"latest_run: {run.id} status={run.status.value}"
        result = run.result or {}
        fos = result.get("factor_of_safety")
        if fos is not None:
            summary += f" factor_of_safety={fos}"
        peak = result.get("max_von_mises_mpa")
        if peak is not None:
            summary += f" max_von_mises_mpa={peak}"
        if run.error:
            summary += f" error={_clean(run.error)}"
        lines.append(summary)
    if active:
        # The single most common way to waste real compute is to queue a second
        # run because the first one's status never made it back into context.
        lines.append(f"runs_in_flight: {active} (queued or running right now)")
    return lines


def _catia_lines(
    conversation: Conversation, available: bool | None, document: str | None
) -> list[str]:
    if available is None and not document:
        return []

    lines: list[str] = []
    if available is True:
        lines.append("catia_bridge: connected")
    elif available is False:
        # Two different things used to be collapsed into "no CATIA tool will
        # work", and the difference decides what the assistant should do next.
        # Told only that the bridge was down, the model concluded it had no
        # CATIA at all and asked the user to upload a STEP file, which is
        # precisely the hand-off this product exists to remove.
        #
        # On a single-machine install there is nothing for the user to do at
        # all: Kryova starts and pairs the daemon itself, and the only thing it
        # is ever waiting for is CATIA being open -- which the assistant can fix
        # by itself with open_in_catia. Anything about pairing a workstation is
        # false there, and it was still being said to a user with CATIA on
        # screen, so the local case gets its own line rather than a qualifier on
        # the remote one.
        if _local_bridge_supported():
            lines.append(
                "catia_bridge: attaching -- Kryova runs the bridge on this machine "
                "and pairs it itself, so there is nothing for the user to set up "
                "and nothing to ask them for. It is waiting for CATIA to be open. "
                "Call open_in_catia, then go straight on to the catia_* modelling "
                "tools: the bridge attaches within a few seconds and those tools "
                "wait for it. Never ask the user to pair a workstation, and never "
                "ask them to upload or model the part themselves."
            )
        else:
            lines.append(
                "catia_bridge: not connected -- the catia_* modelling tools are "
                "unavailable until a workstation is paired and the Kryova bridge is "
                "running. open_in_catia still works: it starts CATIA on this machine, "
                "and the bridge attaches by itself once CATIA is up. Offer that "
                "before asking the user for a CAD file."
            )

    if not document:
        # Phrased so that dropping the qualifier cannot produce a falsehood.
        # "none bound to this conversation yet" was accurate and got paraphrased
        # into "there is no document open in CATIA" -- said to a user who had
        # CATIA in front of them with a part open. The binding is a fact about
        # this conversation, not about CATIA, and the line has to survive being
        # compressed into one clause.
        lines.append(
            "catia_document: this conversation has not created or opened a part yet. "
            "This says nothing about what CATIA itself has open -- do not tell the "
            "user their CATIA is empty. Call catia_new_part to start a part this "
            "conversation owns, before any other geometry operation."
        )
        return lines

    lines.append(
        f"catia_document: {_clean(document)} -- bound to this conversation; if you "
        "have not opened it in this session, call catia_open_document first"
    )
    state = conversation.catia_state or {}
    # The material is a decision, not a measurement, and it is the one thing the
    # transcript held that nothing here reported. Asked "what material did we
    # pick?" eight turns after "steel gearbox mounting plate", the assistant read
    # CATIA -- which has no material applied unless the workstation is licensed
    # for the Material Library -- and answered "none has been assigned, which
    # grade would you like?", re-asking a question the user had already answered.
    material = state.get("material")
    if material:
        density = state.get("density_kg_m3")
        suffix = f" ({_clean(density)} kg/m3)" if density else ""
        lines.append(f"catia_material: {_clean(material)}{suffix} -- already chosen, do not re-ask")
    else:
        lines.append(
            "catia_material: not set yet -- masses are provisional until "
            "catia_set_material is called"
        )
    features = state.get("features")
    if isinstance(features, list) and features:
        lines.append(f"catia_features: {', '.join(_clean(f) for f in features[:20])}")
    parameters = state.get("parameters")
    if isinstance(parameters, dict) and parameters:
        rendered = ", ".join(
            f"{_clean(name)}={_clean(value)}" for name, value in list(parameters.items())[:20]
        )
        lines.append(f"catia_parameters: {rendered}")
    elif isinstance(parameters, list) and parameters:
        lines.append(f"catia_parameters: {', '.join(_clean(p) for p in parameters[:20])}")
    for key, label in (
        ("mass_kg", "catia_mass_kg"),
        ("volume_mm3", "catia_volume_mm3"),
        ("bounding_box_mm", "catia_bounding_box_mm"),
    ):
        value = state.get(key)
        if value is not None:
            # `_clean` like every other field above, and for the same reason.
            # These three are copied verbatim out of bridge tool results
            # (`tools.py` CATIA_STATE_KEYS), so they are attacker-influenced
            # text, not server-computed numbers. Interpolating them raw let a
            # crafted mass property close `</current_state>` early and have
            # everything after it read as server authority -- inside the one
            # block the prompt tells the model to trust.
            lines.append(f"{label}: {_clean(value)}")
    return lines


def build_state_block(db: Session, user: User, conversation: Conversation) -> str:
    """Render the current truth for one conversation, as fenced text.

    Returns the block including its delimiters. It is injected as a message
    rather than into the system prompt on purpose: the system prompt is a frozen
    cache prefix, and a block that changes every turn would invalidate it and
    every cached token behind it.
    """
    lines: list[str] = [f"conversation: {conversation.id}"]

    project: Project | None = None
    if conversation.project_id:
        candidate = db.get(Project, conversation.project_id)
        # Re-check ownership rather than trusting the stored id: a project can
        # be deleted or, in principle, re-created under another owner.
        if candidate is not None and candidate.owner_id == user.id:
            project = candidate

    if project is None:
        owned = (
            db.scalar(select(func.count()).select_from(Project).where(Project.owner_id == user.id))
            or 0
        )
        lines.append(
            "project: none selected for this conversation "
            f"(the user owns {owned} project(s); call list_projects to see them)"
        )
    else:
        lines.extend(_project_lines(db, project))

    lines.extend(
        _catia_lines(
            conversation,
            _catia_available(db, user.id),
            bound_document_name(db, conversation.id),
        )
    )

    body = "\n".join(lines)
    return f"{STATE_OPEN}\n{_PREAMBLE}\n\n{body}\n{STATE_CLOSE}"
