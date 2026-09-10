"""Request and response shapes for approval gates (P5.5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.gates import DEFAULT_GATE_DAYS
from app.models import GateState


class GateCreate(BaseModel):
    title: str = Field(max_length=255)
    question: str = Field(max_length=4000)
    subject_type: str = Field(max_length=32)
    subject_id: str = Field(max_length=64)
    #: The thing being approved. Digested server-side — the client never sends a
    #: digest, because a digest the client chose is a digest the client can make
    #: match anything.
    subject: Any
    #: What the reviewer will be shown: a spec diff, the assertions it reaches,
    #: a cost estimate. Free-form because the shapes come from `design/diff.py`
    #: and `verify/`, which own their own vocabulary.
    evidence: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None
    conversation_id: str | None = None
    expires_in_days: int = Field(default=DEFAULT_GATE_DAYS, ge=1, le=90)


class GateDecision(BaseModel):
    approve: bool
    #: Required on a rejection, and `core/gates.decide` refuses without one
    #: rather than trusting this to be checked here — the agent tools reach
    #: `decide` without passing through this model.
    note: str | None = Field(default=None, max_length=4000)
    #: The subject as the decider sees it *now*. Re-digested and compared, so an
    #: approval cannot land on something that moved while it was pending.
    subject: Any


class GateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    question: str
    state: GateState
    subject_type: str
    subject_id: str
    subject_digest: str
    evidence: dict[str, Any]
    project_id: str | None
    conversation_id: str | None
    requested_by_id: str
    created_at: datetime
    expires_at: datetime | None
    decided_at: datetime | None
    decided_by_id: str | None
    decision_note: str | None


class GatePage(BaseModel):
    items: list[GateRead]
    total: int
    page: int
    page_size: int


__all__ = ["GateCreate", "GateDecision", "GatePage", "GateRead"]
