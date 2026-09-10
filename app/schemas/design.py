"""Request and response shapes for the persisted design record (P5 tasks 3 and 6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DesignRead(BaseModel):
    """The head of a design, with the spec inline.

    `document` is `DesignSpec.to_dict()` and is deliberately typed loosely here.
    Mirroring the spec's shape into a Pydantic model would be a second schema
    for it — one that goes stale silently the first time a field is added to
    `app/design/spec.py`, which is exactly the defect the frontend's hand-written
    `types/api.ts` already carries and does not need a second instance of. The
    spec validates itself on load, and `format_version` is what refuses a shape
    this build does not understand.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    project_id: str | None
    name: str
    digest: str
    revision_number: int
    document: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class RevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    revision_number: int
    digest: str
    summary: str
    author: str
    author_id: str | None
    created_at: datetime


class RevisionPage(BaseModel):
    items: list[RevisionRead]
    total: int
    page: int
    page_size: int


class ParameterEdit(BaseModel):
    """One decision changed.

    A single parameter rather than a whole spec, on purpose. Accepting a spec
    from the client would mean the client had authored a design the server never
    compiled, and the first malformed one would arrive as a compile error on a
    revision that had already been written.
    """

    value: float


class DesignEdited(BaseModel):
    """What an edit did, including when it did nothing.

    `changed` and `plan_changed` are different questions and both are worth an
    answer. An edit can move the spec (a rationale note rewritten) without
    changing the part, and reporting that as "nothing happened" would lose an
    edit somebody made on purpose.
    """

    design: DesignRead
    changed: bool
    diff: dict[str, Any] | None = None


class DesignDiffRead(BaseModel):
    from_revision: int
    to_revision: int
    diff: dict[str, Any]


class DesignSummary(BaseModel):
    """Enough to list designs without deserialising every spec."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    name: str
    digest: str
    revision_number: int
    updated_at: datetime


class DesignPage(BaseModel):
    items: list[DesignSummary]
    total: int
    page: int
    page_size: int = Field(default=20)


__all__ = [
    "DesignDiffRead",
    "DesignEdited",
    "DesignPage",
    "DesignRead",
    "DesignSummary",
    "ParameterEdit",
    "RevisionPage",
    "RevisionRead",
]
