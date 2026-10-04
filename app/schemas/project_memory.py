"""Request and response shapes for project memory (ROAD_TO_10 2.7)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.project_memory import MAX_FACTS_PER_PROJECT, MAX_TEXT_CHARS


class MemoryCreate(BaseModel):
    """A fact the person typed. It is confirmed the moment it is saved."""

    # The length is checked by `project_memory.clean` as well, which says how to shorten
    # it; this is the backstop that keeps a megabyte out of the parser.
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS * 4)


class MemoryUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS * 4)


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    text: str
    state: Literal["proposed", "confirmed"]
    author: Literal["user", "agent"]
    author_id: str | None = Field(
        description="Null with author 'agent' means the agent, not 'unknown'."
    )
    conversation_id: str | None = Field(
        description="The conversation the agent noticed it in, while that still exists."
    )
    created_at: datetime
    confirmed_at: datetime | None


class MemoryPage(BaseModel):
    items: list[MemoryRead]
    total: int
    page: int
    page_size: int
    #: The most a project keeps, so the panel can say "12 of 40" without hard-coding it.
    limit: int = MAX_FACTS_PER_PROJECT
