"""Project memory: the behaviour (ROAD_TO_10 2.7). The model says why it exists.

Four rules, each pinned by a test that fails when it is removed:

1. **Only a person's confirmation makes a fact readable by the model.** `propose` writes a
   `PROPOSED` row and nothing here lets a proposal be quoted; `confirm` is the one door, and
   it records who went through it.
2. **A proposal cannot flood the user.** Open proposals are capped (`MAX_OPEN_PROPOSALS`),
   and a proposal that repeats a fact already recorded -- confirmed or waiting -- returns the
   existing row instead of a second one, so an agent that re-proposes every turn costs the
   user one line, not forty.
3. **A fact is one short sentence.** `MAX_TEXT_CHARS`. A paragraph is a document, and a
   document belongs in an attachment, where it is quoted as data and cited.
4. **The project's organisation is read off the project**, never taken from the caller, so a
   fact cannot be filed under an organisation its project is not in.

Nothing here commits: the route and the tool own the transaction, as `designs.py` does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MemoryState, Project, ProjectMemory, User
from app.models.base import utcnow

#: One sentence, not a paragraph. Long enough for "Bolts are ISO 4762 A2-70 stainless,
#: M6 clearance holes 6.6 mm, unless a drawing says otherwise" and not for a datasheet.
MAX_TEXT_CHARS = 300
#: Confirmed and proposed together. Every confirmed fact is sent on every step of every turn,
#: so this is a bound on a bill as well as on the user's attention.
MAX_FACTS_PER_PROJECT = 40
#: What the user has to review. An agent that has noticed ten things worth remembering in one
#: conversation has noticed too much.
MAX_OPEN_PROPOSALS = 8


class MemoryRefusal(ValueError):
    """The request cannot be done, with the reason in words. Maps to 409, or 422 for `text`."""


class BadText(MemoryRefusal):
    """The sentence itself is unusable: empty or too long. Maps to 422."""


def clean(text: str) -> str:
    """One line, single-spaced. A fact with a newline in it can pose as a second fact."""
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    if not collapsed:
        raise BadText("A project fact cannot be empty.")
    if len(collapsed) > MAX_TEXT_CHARS:
        raise BadText(
            f"A project fact is one short sentence: at most {MAX_TEXT_CHARS} characters, and "
            f"this is {len(collapsed)}. Shorten it, or attach the document it comes from."
        )
    return collapsed


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def facts(db: Session, project_id: str) -> list[ProjectMemory]:
    """Every fact of a project, in the order they were written."""
    return list(
        db.scalars(
            select(ProjectMemory)
            .where(ProjectMemory.project_id == project_id)
            .order_by(ProjectMemory.created_at, ProjectMemory.id)
        )
    )


def confirmed(db: Session, project_id: str) -> list[ProjectMemory]:
    """The facts the model may read: confirmed ones only, oldest first (a stable order)."""
    return [f for f in facts(db, project_id) if f.state is MemoryState.CONFIRMED]


def get(db: Session, project: Project, memory_id: str) -> ProjectMemory | None:
    """A fact of *this* project. Another project's id is not found, never "not yours"."""
    row = db.get(ProjectMemory, memory_id)
    return row if row is not None and row.project_id == project.id else None


@dataclass(frozen=True, slots=True)
class Written:
    memory: ProjectMemory
    #: False when the sentence was already recorded and the existing row came back.
    created: bool


def _room_for_one_more(existing: list[ProjectMemory]) -> None:
    if len(existing) >= MAX_FACTS_PER_PROJECT:
        raise MemoryRefusal(
            f"This project already holds {MAX_FACTS_PER_PROJECT} facts, which is the most it "
            "keeps: every one is read on every step. Delete one that no longer matters first."
        )


def create(db: Session, project: Project, user: User, text: str) -> Written:
    """A fact a person typed. Confirmed at once: they wrote it, so they have seen it."""
    sentence = clean(text)
    existing = facts(db, project.id)
    for fact in existing:
        if _same(fact.text, sentence):
            if fact.state is MemoryState.PROPOSED:
                # They typed what the agent proposed: that is a confirmation.
                confirm(db, fact, user)
            return Written(fact, created=False)
    _room_for_one_more(existing)
    now = utcnow()
    row = ProjectMemory(
        organisation_id=project.organisation_id,
        project_id=project.id,
        text=sentence,
        state=MemoryState.CONFIRMED,
        author="user",
        author_id=user.id,
        confirmed_by_id=user.id,
        confirmed_at=now,
    )
    db.add(row)
    db.flush()
    return Written(row, created=True)


def propose(db: Session, project: Project, conversation_id: str | None, text: str) -> Written:
    """A fact the agent noticed. Waits for a person; the model cannot read it until then."""
    sentence = clean(text)
    existing = facts(db, project.id)
    for fact in existing:
        if _same(fact.text, sentence):
            return Written(fact, created=False)
    open_proposals = sum(1 for f in existing if f.state is MemoryState.PROPOSED)
    if open_proposals >= MAX_OPEN_PROPOSALS:
        raise MemoryRefusal(
            f"{open_proposals} proposals are already waiting for the user to confirm or "
            "dismiss. Do not propose more until they have; say in your answer what you "
            "noticed instead."
        )
    _room_for_one_more(existing)
    row = ProjectMemory(
        organisation_id=project.organisation_id,
        project_id=project.id,
        text=sentence,
        state=MemoryState.PROPOSED,
        author="agent",
        author_id=None,
        conversation_id=conversation_id,
    )
    db.add(row)
    db.flush()
    return Written(row, created=True)


def edit(db: Session, memory: ProjectMemory, text: str) -> ProjectMemory:
    """Reword a fact. A confirmed one stays confirmed: the person who edits it has read it."""
    sentence = clean(text)
    for fact in facts(db, memory.project_id):
        if fact.id != memory.id and _same(fact.text, sentence):
            raise MemoryRefusal("The project already holds that fact.")
    memory.text = sentence
    db.flush()
    return memory


def confirm(db: Session, memory: ProjectMemory, user: User) -> ProjectMemory:
    """The one door from `PROPOSED` to readable. Idempotent, and keeps the first confirmer."""
    if memory.state is MemoryState.CONFIRMED:
        return memory
    memory.state = MemoryState.CONFIRMED
    memory.confirmed_by_id = user.id
    memory.confirmed_at = utcnow()
    db.flush()
    return memory


def forget(db: Session, memory: ProjectMemory) -> None:
    db.delete(memory)
    db.flush()

