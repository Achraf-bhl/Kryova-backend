from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    owner_id: str
    #: The tenant that owns this project, and the one that gets billed for a run
    #: against it. Added 2026-09-10 for P5.7: the cost estimate is per
    #: organisation (`GET /organisations/{id}/billing/estimate`), so a page that
    #: knows only the project cannot ask what a run will cost — and "how much
    #: will this be" is a question that has to be answerable *before* the run,
    #: which is the whole of that task.
    #:
    #: `owner_id` is not a substitute. Since P2 a project belongs to an
    #: organisation and can be transferred between them, so the owner and the
    #: billed tenant are two different facts that happen to coincide on a
    #: personal team.
    organisation_id: str
    created_at: datetime
    updated_at: datetime
