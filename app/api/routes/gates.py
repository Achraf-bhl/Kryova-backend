"""Approval gates: raising them, listing them, deciding them (P5.5).

Scoped under an organisation rather than a project, because a gate can be about
a conversation that has not chosen a project yet, and because the reviewer
authorised to decide it is a property of the *organisation* membership.

**This is the first reader of `Membership.domain_role`.** That column has
existed since P2.2 and its own docstring said "16.5/P5 read this column" — it
was written for this route and nothing had ever consulted it, which is the same
shape of defect as a solver that could be configured but never asked for. A
sign-off from somebody without `REVIEWER` is not a sign-off, and `None` means
*not stated* and does not qualify: a nullable column defaulted into meaning
"yes" would silently qualify everybody, which is the failure the column was
made nullable to avoid.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import func, select

from app.api.deps import AuditDep, CurrentUser, DbSession, PrincipalDep, ViewerOrganisation
from app.core import gates
from app.core.config import settings
from app.models import ApprovalGate, GateState, Organisation
from app.models.audit import AuditAction, AuditOutcome
from app.models.organisation import DomainRole, membership_for_user
from app.schemas.gates import GateCreate, GateDecision, GatePage, GateRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/organisations/{organisation_id}/gates", tags=["gates"])

_NOT_FOUND = "Approval gate not found."


def _gate_or_404(db: DbSession, organisation: Organisation, gate_id: str) -> ApprovalGate:
    gate = db.get(ApprovalGate, gate_id)
    # 404 rather than 403 for a gate in another organisation, the same rule the
    # rest of the service follows: an id a caller cannot address is, for them,
    # not there.
    if gate is None or gate.organisation_id != organisation.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return gate


@router.post("", response_model=GateRead, status_code=status.HTTP_201_CREATED)
def raise_gate(
    payload: GateCreate,
    organisation: ViewerOrganisation,
    current_user: CurrentUser,
    db: DbSession,
) -> ApprovalGate:
    """Ask for a sign-off on something, pinned to it as it stands now.

    Any member may raise one, including a viewer. Raising a gate asks a
    question; it grants nothing, and a permission check here would mean the
    person who noticed the problem is the one who cannot report it.
    """
    gate = gates.raise_gate(
        db,
        organisation_id=organisation.id,
        requested_by=current_user,
        title=payload.title,
        question=payload.question,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
        subject=payload.subject,
        evidence=payload.evidence,
        project_id=payload.project_id,
        conversation_id=payload.conversation_id,
        expires_in_days=payload.expires_in_days,
    )
    db.commit()
    return gate


@router.get("", response_model=GatePage)
def list_gates(
    organisation: ViewerOrganisation,
    db: DbSession,
    state: GateState | None = None,
    conversation_id: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> GatePage:
    """Gates in this organisation, newest first.

    Sweeps expiries first, so a list of pending gates cannot fill with rows
    nobody is able to act on. It is a write on a read path, which is worth the
    exception: the alternative is a filter, and a gate that merely stops
    matching a query never *expired* — nothing recorded that a decision went
    unmade, which is the fact somebody needs later.
    """
    gates.expire_due(db)

    query = select(ApprovalGate).where(ApprovalGate.organisation_id == organisation.id)
    if state is not None:
        query = query.where(ApprovalGate.state == state)
    if conversation_id is not None:
        query = query.where(ApprovalGate.conversation_id == conversation_id)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = list(
        db.scalars(
            query.order_by(ApprovalGate.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return GatePage(
        items=[GateRead.model_validate(g) for g in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{gate_id}", response_model=GateRead)
def read_gate(
    organisation: ViewerOrganisation,
    db: DbSession,
    gate_id: Annotated[str, Path()],
) -> ApprovalGate:
    return _gate_or_404(db, organisation, gate_id)


@router.post("/{gate_id}/decision", response_model=GateRead)
def decide_gate(
    payload: GateDecision,
    organisation: ViewerOrganisation,
    current_user: CurrentUser,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
    gate_id: Annotated[str, Path()],
) -> ApprovalGate:
    """Approve or reject, with the actor recorded.

    Audited on **both** outcomes and on the refusal. A rejection is as much a
    decision as an approval, and an attempt to approve one's own gate is exactly
    the event an auditor comes looking for.
    """
    gate = _gate_or_404(db, organisation, gate_id)

    membership = membership_for_user(db, current_user, organisation.id)
    if membership is None or membership.domain_role is not DomainRole.REVIEWER:
        # 403, not 404: the caller can see this gate — they are in the
        # organisation and the list endpoint showed it to them — so hiding it
        # here would be incoherent rather than discreet. What they lack is a
        # role, and saying which role is what lets them go and ask for it.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Deciding an approval gate needs the reviewer domain role. Ask an "
                "organisation admin to grant it."
            ),
        )

    decision = gates.decide(
        db,
        gate,
        approve=payload.approve,
        by=current_user,
        subject=payload.subject,
        note=payload.note,
        allow_self_approval=settings.allow_self_approval,
    )
    if not decision.ok:
        assert decision.refusal is not None
        audit.record(
            AuditAction.GATE_DECIDED,
            AuditOutcome.REFUSED,
            principal=principal,
            target_type="approval_gate",
            target_id=gate.id,
            reason=decision.refusal.value,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=decision.refusal.sentence
        )

    audit.record(
        AuditAction.GATE_DECIDED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="approval_gate",
        target_id=gate.id,
        reason=payload.note or None,
        detail={"state": gate.state.value, "subject_type": gate.subject_type},
    )
    db.commit()
    return gate
