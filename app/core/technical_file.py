"""Gathering a design's record into a technical-file contribution — master plan
E19 task 3, the half that reads the database.

`app.compliance.technical_file` knows the law and the shape; this reads the rows
and hands it plain data. Everything included is something the product already
recorded at the time — the spec, its revisions, the operations that built it,
the analyses, the sign-off decisions. Nothing is recomputed into a claim here
except the build plan, which is compiled from the head spec because a plan is
the spec's deterministic consequence and is not stored.

**The analyses are the project's, and the file says so.** A `SimulationJob`
belongs to a project and a geometry version; nothing records which design
revision a geometry came from. So the file lists every analysis run in the
design's project, names the geometry each ran on, and states that the link to
this design is not recorded — rather than implying one by putting the analyses
under the design's name.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.compliance import technical_file as tf
from app.core import designs
from app.design.compile import compile_spec
from app.design.errors import SpecError
from app.models import (
    ApprovalGate,
    CatiaOperation,
    DesignDocument,
    DesignRevision,
    GeometryVersion,
    JobStatus,
    SimulationJob,
)
from app.verify.standards import NOT_VALIDATED

_ANALYSES_LINK = (
    "Every analysis run in this design's project. A simulation records the geometry version it "
    "ran on and not the design revision that geometry came from, so which of these analysed this "
    "design is not recorded; each names its geometry so a reader can decide."
)


def _iso(value: datetime.datetime | datetime.date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _specification(document: DesignDocument) -> tf.Artefact:
    return tf.Artefact(
        tf.SPECIFICATION,
        "The design specification at the head revision, verbatim (DesignSpec.to_dict()).",
        document.document,
    )


def _plan(document: DesignDocument) -> tf.Artefact:
    try:
        plan = compile_spec(designs.spec_of(document))
    except SpecError as exc:
        # A spec that compiled when it was saved and does not compile now means
        # the operation registry moved underneath it. That is worth saying in
        # the file, not a reason to refuse the export.
        content: dict[str, Any] = {"compiled": False, "reason": str(exc)}
    else:
        content = {"compiled": True, "plan_digest": plan.digest(), **plan.to_dict()}
    return tf.Artefact(
        tf.BUILD_PLAN,
        "The head specification compiled into the ordered operations that build it, with every "
        "argument resolved. Compiled at export time; a plan is not stored.",
        content,
    )


def _revisions(db: Session, document: DesignDocument) -> tf.Artefact:
    rows = db.scalars(
        select(DesignRevision)
        .where(DesignRevision.design_id == document.id)
        .order_by(DesignRevision.revision_number)
    )
    placed = document.placed_on_market_on
    return tf.Artefact(
        tf.REVISIONS,
        "Every revision of the design, oldest first. author 'agent' with a null author_id is the "
        "agent, not an unknown person.",
        [
            {
                "revision": row.revision_number,
                "digest": row.digest,
                "summary": row.summary,
                "author": row.author,
                "author_id": row.author_id,
                "at": _iso(row.created_at),
                "after_placing_on_market": placed is not None and row.created_at.date() >= placed,
            }
            for row in rows
        ],
    )


def _operations(db: Session, document: DesignDocument) -> tf.Artefact:
    rows = db.scalars(
        select(CatiaOperation)
        .where(CatiaOperation.conversation_id == document.conversation_id)
        .order_by(CatiaOperation.created_at)
    )
    return tf.Artefact(
        tf.OPERATIONS,
        "Every geometry operation run for this design's conversation, failed ones included, in the "
        "order they ran. Arguments and results as logged, which bounds them in size.",
        [
            {
                "tool": row.tool,
                "arguments": row.arguments,
                "ok": row.ok,
                "error": row.error,
                "at": _iso(row.created_at),
            }
            for row in rows
        ],
    )


def _analyses(db: Session, document: DesignDocument, *, project_readable: bool) -> tf.Artefact:
    runs: list[dict[str, Any]] = []
    if document.project_id is not None and not project_readable:
        # The conversation still names a project its owner can no longer read —
        # removed from the organisation, say. The export is the owner's, and the
        # project's analyses are not.
        return tf.Artefact(
            tf.ANALYSES,
            "This design's project is not readable by whoever exported it, so its analyses are "
            "not included.",
            {"validation": NOT_VALIDATED, "runs": runs},
        )
    if document.project_id is not None:
        rows = db.scalars(
            select(SimulationJob)
            .where(SimulationJob.project_id == document.project_id)
            .options(selectinload(SimulationJob.geometry_version).joinedload(GeometryVersion.media))
            .order_by(SimulationJob.created_at)
        )
        for job in rows:
            geometry = job.geometry_version
            runs.append(
                {
                    "id": job.id,
                    "analysis": job.analysis,
                    "status": JobStatus(job.status).value,
                    "solver": job.solver,
                    "solver_version": job.solver_version,
                    "geometry": (
                        {
                            "filename": geometry.filename,
                            "version": geometry.version_number,
                            "sha256": geometry.media.sha256,
                        }
                        if geometry is not None
                        else None
                    ),
                    "load_case": job.load_case,
                    "thermal_case": job.thermal_case,
                    "element_size_mm": job.element_size_mm,
                    "element_order": job.element_order,
                    "grids": job.grids,
                    "thickness_mm": job.thickness_mm,
                    "mesh": job.mesh_stats,
                    "result": job.result,
                    "error": job.error,
                    "finished_at": _iso(job.finished_at),
                }
            )
    return tf.Artefact(
        tf.ANALYSES,
        _ANALYSES_LINK if document.project_id is not None else "This design has no project, so no analyses.",
        {"validation": NOT_VALIDATED, "runs": runs},
    )


def _approvals(db: Session, document: DesignDocument) -> tf.Artefact:
    rows = db.scalars(
        select(ApprovalGate)
        .where(ApprovalGate.conversation_id == document.conversation_id)
        .order_by(ApprovalGate.created_at)
    )
    return tf.Artefact(
        tf.APPROVALS,
        "Sign-off gates raised in this design's conversation. A gate pins the digest of what it "
        "asked about; a decision is a person's, and none of these is a conformity assessment.",
        [
            {
                "title": gate.title,
                "question": gate.question,
                "state": str(gate.state),
                "subject_type": gate.subject_type,
                "subject_digest": gate.subject_digest,
                "requested_by_id": gate.requested_by_id,
                "decided_by_id": gate.decided_by_id,
                "decided_at": _iso(gate.decided_at),
                "decision_note": gate.decision_note,
            }
            for gate in rows
        ],
    )


def build(
    db: Session, document: DesignDocument, *, now: datetime.datetime, project_readable: bool
) -> dict[str, Any]:
    """The technical-file contribution for one design, as of `now`.

    `project_readable` is the caller's answer to whether the exporting user may
    read the design's project; this module does not decide access.
    """
    notice = designs.notice_for(document, now.date())
    return tf.assemble(
        design={
            "name": document.name,
            "conversation_id": document.conversation_id,
            "project_id": document.project_id,
            "digest": document.digest,
            "revision": document.revision_number,
            "placed_on_market_on": _iso(document.placed_on_market_on),
            "modification": notice.to_dict() | {"placed_on_market_on": _iso(notice.placed_on_market_on)},
        },
        artefacts=[
            _specification(document),
            _plan(document),
            _revisions(db, document),
            _operations(db, document),
            _analyses(db, document, project_readable=project_readable),
            _approvals(db, document),
        ],
        statements={"validation": NOT_VALIDATED},
        generated_at=now,
    )


__all__ = ["build"]
