from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    #: Replaces the whole tag list; normalised and bounded by `app.core.projects`.
    tags: list[str] | None = Field(default=None, max_length=50)
    #: True puts the project away, False brings it back. Nothing is deleted either way.
    archived: bool | None = None


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
    #: When it was put away, or null (ROAD_TO_10 7.1).
    archived_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    #: The mission rung it was started from, if any (7.7).
    template_key: str | None = None
    #: Whether *the requesting user* starred it. Per person, never per project (7.6), and
    #: filled in by the route that knows who is asking -- false on a bare model read.
    starred: bool = False


class ProjectDuplicate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)


class ProjectDuplicated(BaseModel):
    project: ProjectRead
    geometry_versions: int
    designs: int
    #: What the copy does not carry, in words -- so "did it copy my runs" has an answer in
    #: the response and not in the reader's assumption.
    left_out: list[str]


class ActivityEntryRead(BaseModel):
    at: datetime
    kind: str
    summary: str
    #: `user`, `agent`, or `unknown` -- a row that records no author says so rather than
    #: being credited to the project's owner.
    actor_kind: str
    actor_user_id: str | None = None
    actor_email: str | None = None
    target_type: str | None = None
    target_id: str | None = None


class ActivityPage(BaseModel):
    items: list[ActivityEntryRead]
    #: Pass as `before` for the next page; null when this page was the last.
    next_before: datetime | None = None


class ProjectTemplateRead(BaseModel):
    key: str
    title: str
    era: str
    kind: str
    hard: str
    claims: list[str]
    #: What starting from this rung does NOT prove. Always shown beside the claims.
    unproven: list[str]
    seeds_a_design: bool


class ProjectFromTemplate(BaseModel):
    template: str = Field(min_length=1, max_length=16)
    name: str | None = Field(default=None, min_length=1, max_length=255)


class ProjectFromTemplateRead(BaseModel):
    project: ProjectRead
    template: ProjectTemplateRead
    #: The conversation holding the seeded design, or null for an assembly or mechanism.
    conversation_id: str | None = None
    design_note: str = ""


class ProjectImported(BaseModel):
    project: ProjectRead
    geometry_versions: int
    designs: int
    not_restored: list[str]
