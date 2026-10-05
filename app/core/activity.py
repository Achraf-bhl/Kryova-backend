"""A project's activity feed: who did what, read from what already records it (ROAD_TO_10 7.5).

**The audit log alone cannot answer "what happened in this project".** It records gate
decisions, cancellations and transfers (`AuditAction`), and nothing about a design edit, a
geometry upload or a run being queued -- those are written to their own tables with their own
timestamps and, where there is one, their own author. So the feed is a *merge*: audit rows for
the project's targets, plus the rows of the tables that are themselves the record.

Three rules, each because the opposite is a believable lie:

1. **An entry names its actor only where a row says who.** A `GeometryVersion` and a
   `SimulationJob` carry no user, so their entries say `actor_kind="unknown"` rather than
   guessing the project's owner. A design revision's author is `user` or `agent`
   (`DesignRevision.author`; a null `author_id` means the agent, never "unknown").
2. **A gate's decision comes from the gate, not from the audit row.** Both exist and a feed
   carrying both reports one approval twice. The audit rows kept are the ones with no other
   home: a *refused* decision, a cancellation, a transfer.
3. **It is a window, not a ledger.** Each source is read newest-first up to `limit` rows before
   the cursor, the union is sorted and cut to `limit`, so a page costs six small indexed
   queries however long the project's history is. The cursor is a timestamp, so two entries
   with the very same microsecond straddling a page edge can be skipped -- the price of not
   holding a server-side cursor, stated here rather than discovered later.

Nothing here writes, and nothing here is tenant-aware by itself: the caller has already
resolved the project through `ReadableProject`, which is the tenancy check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    ApprovalGate,
    DesignDocument,
    DesignRevision,
    GeometryVersion,
    Project,
    ProjectMemory,
    SimulationJob,
    User,
)
from app.models.audit import AuditAction, AuditEvent, AuditOutcome

ActorKind = Literal["user", "agent", "unknown"]

MAX_LIMIT = 100
DEFAULT_LIMIT = 30


@dataclass(frozen=True)
class Entry:
    """One thing that happened, in the words a reader of a project page wants."""

    at: datetime
    #: A dotted kind a client can filter or icon on: `design.revision`, `geometry.uploaded`,
    #: `simulation.queued`, `gate.requested`, `gate.decided`, `memory.proposed`,
    #: `memory.confirmed`, and the audit kinds (`run.cancelled`, `project.transferred`,
    #: `gate.refused`).
    kind: str
    summary: str
    actor_kind: ActorKind
    actor_user_id: str | None = None
    actor_email: str | None = None
    target_type: str | None = None
    target_id: str | None = None


def feed(
    db: Session,
    project: Project,
    *,
    limit: int = DEFAULT_LIMIT,
    before: datetime | None = None,
) -> list[Entry]:
    """The newest `limit` entries strictly before `before`, newest first."""
    limit = max(1, min(limit, MAX_LIMIT))
    entries: list[Entry] = []
    for source in (
        _design_revisions,
        _geometry,
        _simulations,
        _gates,
        _memory,
        _audit,
    ):
        entries.extend(source(db, project, limit, before))
    entries.sort(key=lambda entry: (entry.at, entry.kind, entry.target_id or ""), reverse=True)
    return entries[:limit]


def _emails(db: Session, user_ids: set[str | None]) -> dict[str, str]:
    wanted = {user_id for user_id in user_ids if user_id}
    if not wanted:
        return {}
    return {
        user.id: user.email for user in db.scalars(select(User).where(User.id.in_(wanted)))
    }


def _by_user(user_id: str | None, emails: dict[str, str]) -> dict[str, object]:
    """The actor fields for an entry whose row names a user, or none."""
    if user_id is None:
        return {"actor_kind": "unknown"}
    return {"actor_kind": "user", "actor_user_id": user_id, "actor_email": emails.get(user_id)}


def _design_revisions(
    db: Session, project: Project, limit: int, before: datetime | None
) -> list[Entry]:
    stmt = (
        select(DesignRevision, DesignDocument)
        .join(DesignDocument, DesignDocument.id == DesignRevision.design_id)
        .where(DesignDocument.project_id == project.id)
        .order_by(DesignRevision.created_at.desc())
        .limit(limit)
    )
    if before is not None:
        stmt = stmt.where(DesignRevision.created_at < before)
    rows = db.execute(stmt).all()
    emails = _emails(db, {revision.author_id for revision, _ in rows})
    entries = []
    for revision, document in rows:
        if revision.revision_number == 1:
            summary = f"Started the design {document.name!r}."
        else:
            summary = (
                f"Changed {document.name!r} to revision {revision.revision_number}"
                + (f": {revision.summary}" if revision.summary else ".")
            )
        # `author` is "user" or "agent"; a null author_id means the agent, not "unknown".
        actor = (
            {"actor_kind": "agent"}
            if revision.author == "agent"
            else _by_user(revision.author_id, emails)
        )
        entries.append(
            Entry(
                at=revision.created_at,
                kind="design.revision",
                summary=summary,
                target_type="design_document",
                target_id=document.id,
                **actor,  # type: ignore[arg-type]
            )
        )
    return entries


def _geometry(db: Session, project: Project, limit: int, before: datetime | None) -> list[Entry]:
    stmt = (
        select(GeometryVersion)
        .where(GeometryVersion.project_id == project.id)
        .order_by(GeometryVersion.created_at.desc())
        .limit(limit)
    )
    if before is not None:
        stmt = stmt.where(GeometryVersion.created_at < before)
    return [
        Entry(
            at=version.created_at,
            kind="geometry.uploaded",
            summary=f"Added {version.filename} as geometry version {version.version_number}.",
            actor_kind="unknown",
            target_type="geometry_version",
            target_id=version.id,
        )
        for version in db.scalars(stmt)
    ]


def _simulations(
    db: Session, project: Project, limit: int, before: datetime | None
) -> list[Entry]:
    stmt = (
        select(SimulationJob)
        .where(SimulationJob.project_id == project.id)
        .order_by(SimulationJob.created_at.desc())
        .limit(limit)
    )
    if before is not None:
        stmt = stmt.where(SimulationJob.created_at < before)
    return [
        Entry(
            at=job.created_at,
            kind="simulation.queued",
            summary=f"Queued a {job.analysis} run ({job.status.value}).",
            actor_kind="unknown",
            target_type="simulation_job",
            target_id=job.id,
        )
        for job in db.scalars(stmt)
    ]


def _gates(db: Session, project: Project, limit: int, before: datetime | None) -> list[Entry]:
    requested = select(ApprovalGate).where(ApprovalGate.project_id == project.id)
    decided = requested.where(ApprovalGate.decided_at.is_not(None))
    if before is not None:
        requested = requested.where(ApprovalGate.created_at < before)
        decided = decided.where(ApprovalGate.decided_at < before)
    asked = list(db.scalars(requested.order_by(ApprovalGate.created_at.desc()).limit(limit)))
    answered = list(db.scalars(decided.order_by(ApprovalGate.decided_at.desc()).limit(limit)))
    ids: set[str | None] = {gate.requested_by_id for gate in asked}
    ids |= {gate.decided_by_id for gate in answered}
    emails = _emails(db, ids)
    entries = [
        Entry(
            at=gate.created_at,
            kind="gate.requested",
            summary=f"Asked for approval: {gate.title}",
            target_type="approval_gate",
            target_id=gate.id,
            **_by_user(gate.requested_by_id, emails),  # type: ignore[arg-type]
        )
        for gate in asked
    ]
    for gate in answered:
        assert gate.decided_at is not None
        entries.append(
            Entry(
                at=gate.decided_at,
                kind="gate.decided",
                summary=f"{gate.state.value.capitalize()}: {gate.title}",
                target_type="approval_gate",
                target_id=gate.id,
                **_by_user(gate.decided_by_id, emails),  # type: ignore[arg-type]
            )
        )
    return entries


def _memory(db: Session, project: Project, limit: int, before: datetime | None) -> list[Entry]:
    proposed = select(ProjectMemory).where(ProjectMemory.project_id == project.id)
    confirmed = proposed.where(ProjectMemory.confirmed_at.is_not(None))
    if before is not None:
        proposed = proposed.where(ProjectMemory.created_at < before)
        confirmed = confirmed.where(ProjectMemory.confirmed_at < before)
    first = list(db.scalars(proposed.order_by(ProjectMemory.created_at.desc()).limit(limit)))
    second = list(db.scalars(confirmed.order_by(ProjectMemory.confirmed_at.desc()).limit(limit)))
    emails = _emails(
        db, {fact.author_id for fact in first} | {fact.confirmed_by_id for fact in second}
    )
    entries = []
    for fact in first:
        actor = (
            {"actor_kind": "agent"} if fact.author == "agent" else _by_user(fact.author_id, emails)
        )
        entries.append(
            Entry(
                at=fact.created_at,
                kind="memory.proposed" if fact.author == "agent" else "memory.added",
                summary=f"Noted for the project: {fact.text}",
                target_type="project_memory",
                target_id=fact.id,
                **actor,  # type: ignore[arg-type]
            )
        )
    for fact in second:
        assert fact.confirmed_at is not None
        entries.append(
            Entry(
                at=fact.confirmed_at,
                kind="memory.confirmed",
                summary=f"Confirmed: {fact.text}",
                target_type="project_memory",
                target_id=fact.id,
                **_by_user(fact.confirmed_by_id, emails),  # type: ignore[arg-type]
            )
        )
    return entries


def _audit(db: Session, project: Project, limit: int, before: datetime | None) -> list[Entry]:
    """The audit rows with no other home -- see rule 2."""
    job_ids = select(SimulationJob.id).where(SimulationJob.project_id == project.id)
    gate_ids = select(ApprovalGate.id).where(ApprovalGate.project_id == project.id)
    stmt = (
        select(AuditEvent)
        .where(
            or_(
                (AuditEvent.action == AuditAction.RUN_CANCELLED)
                & AuditEvent.target_id.in_(job_ids),
                (AuditEvent.action == AuditAction.PROJECT_TRANSFERRED)
                & (AuditEvent.target_id == project.id),
                (AuditEvent.action == AuditAction.GATE_DECIDED)
                & (AuditEvent.outcome == AuditOutcome.REFUSED)
                & AuditEvent.target_id.in_(gate_ids),
            )
        )
        .order_by(AuditEvent.occurred_at.desc())
        .limit(limit)
    )
    if before is not None:
        stmt = stmt.where(AuditEvent.occurred_at < before)
    entries = []
    for event in db.scalars(stmt):
        if event.action is AuditAction.RUN_CANCELLED:
            kind, summary = "run.cancelled", "Stopped a run."
        elif event.action is AuditAction.PROJECT_TRANSFERRED:
            kind, summary = "project.transferred", "Moved the project to another organisation."
        else:
            kind = "gate.refused"
            summary = "A decision on an approval gate was refused" + (
                f": {event.reason}." if event.reason else "."
            )
        entries.append(
            Entry(
                at=event.occurred_at,
                kind=kind,
                summary=summary,
                actor_kind="user" if event.actor_user_id else "unknown",
                actor_user_id=event.actor_user_id,
                actor_email=event.actor_email,
                target_type=event.target_type,
                target_id=event.target_id,
            )
        )
    return entries
