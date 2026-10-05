"""What a project may be called, tagged, put away and copied (ROAD_TO_10 Phase 7).

The routes are thin; the rules live here so the agent's tools and the HTTP layer cannot give
different answers to "is this a valid tag" or "what does duplicate copy".

**Duplicate copies what can be copied honestly and says what it did not.** It copies the
geometry (a new `Media` row per version pointing at the *same blob* -- content-addressed, so
a duplicate costs rows, not bytes, and `MediaService.delete` still drops the file only when
the last row goes) and each conversation's *design* (the head spec, as revision 1 of a new
design in a new empty conversation). It does **not** copy: the transcripts (a copy would
carry the old agent's belief about what is in CATIA), the CATIA document (a document is held
by one conversation on one seat -- copying the reference would let two projects drive one
file, the failure `CatiaDocument`'s unique index exists to stop), simulations (a result is
bound to the geometry, mesh, material and solver that produced it, and a copy that kept it
would present another project's evidence as its own), requirements and project memory.
`Duplicated.left_out` names each of those, so the answer to "did it copy my runs" is in the
response and not in the reader's assumption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import designs
from app.models import Conversation, GeometryVersion, Project, ProjectStar, User
from app.models.base import utcnow
from app.models.design import DesignDocument
from app.models.media import Media

#: The name a project gets when nobody has chosen one. The frontend creates it under the same
#: string (`use-agent-chat.ts`), and `name_from_conversation` replaces *only* this -- a name a
#: person typed is never overwritten by a generated one.
DEFAULT_PROJECT_NAME = "New project"

MAX_TAGS = 10
MAX_TAG_LENGTH = 32
_TAG_RE = re.compile(r"[a-z0-9][a-z0-9 ._/+-]*")


class ProjectRefusal(ValueError):
    """A project operation refused, in a sentence the user can act on."""


def normalise_tags(raw: list[str]) -> list[str]:
    """Lower-cased, trimmed, de-duplicated in first-seen order; refuses what cannot be a tag.

    Refuses rather than repairs: a tag silently truncated to 32 characters is a different
    tag from the one the user typed and will not match when they search for it.
    """
    seen: dict[str, None] = {}
    for item in raw:
        tag = " ".join(item.split()).lower()
        if not tag:
            raise ProjectRefusal("A tag cannot be empty.")
        if len(tag) > MAX_TAG_LENGTH:
            raise ProjectRefusal(
                f"The tag {tag[:MAX_TAG_LENGTH]!r}… is longer than {MAX_TAG_LENGTH} characters."
            )
        if not _TAG_RE.fullmatch(tag):
            raise ProjectRefusal(
                f"The tag {tag!r} may use letters, digits, spaces and . _ / + - only, "
                "and must start with a letter or digit."
            )
        seen.setdefault(tag, None)
    if len(seen) > MAX_TAGS:
        raise ProjectRefusal(f"A project holds at most {MAX_TAGS} tags; this one has {len(seen)}.")
    return list(seen)


def name_from_conversation(project: Project, title: str) -> bool:
    """Name an unnamed project after its first conversation. True when it renamed.

    Only a project still called `DEFAULT_PROJECT_NAME`, and only to a real title: the
    placeholder a conversation carries before it has one is not a name.
    """
    cleaned = " ".join(title.split())[:255]
    if project.name != DEFAULT_PROJECT_NAME or not cleaned or cleaned == "New conversation":
        return False
    project.name = cleaned
    return True


def archive(project: Project, *, at: datetime | None = None) -> None:
    """Put a project away. Idempotent: archiving an archived project keeps its first date."""
    if project.archived_at is None:
        project.archived_at = at or utcnow()


def restore(project: Project) -> None:
    project.archived_at = None


def set_star(db: Session, user: User, project: Project, starred: bool) -> bool:
    """Star or unstar for this user. Returns the state afterwards. Idempotent either way."""
    row = db.scalar(
        select(ProjectStar).where(
            ProjectStar.user_id == user.id, ProjectStar.project_id == project.id
        )
    )
    if starred and row is None:
        db.add(ProjectStar(user_id=user.id, project_id=project.id))
    elif not starred and row is not None:
        db.delete(row)
    db.flush()
    return starred


def starred_ids(db: Session, user: User, project_ids: list[str]) -> set[str]:
    """Which of `project_ids` this user has starred, in one query."""
    if not project_ids:
        return set()
    return set(
        db.scalars(
            select(ProjectStar.project_id).where(
                ProjectStar.user_id == user.id, ProjectStar.project_id.in_(project_ids)
            )
        )
    )


def like_pattern(text: str) -> str:
    """`text` as a case-insensitive substring pattern, with `%`, `_` and `\\` escaped."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


#: What a duplicate leaves behind, in the words the response carries.
LEFT_OUT: tuple[str, ...] = (
    "conversation transcripts",
    "the CATIA document (one document belongs to one conversation on one seat)",
    "simulation results (a result is bound to the geometry and run that produced it)",
    "requirements and project memory",
)


@dataclass
class Duplicated:
    project: Project
    geometry_versions: int
    designs: int
    left_out: tuple[str, ...] = field(default=LEFT_OUT)


def duplicate(
    db: Session, source: Project, user: User, *, name: str | None = None
) -> Duplicated:
    """A new project in the same organisation with the source's geometry and designs.

    The caller has already checked the user may read `source` and write to its organisation;
    the copy is owned by `user`. Nothing is committed.
    """
    copy = Project(
        name=(name or f"{source.name} (copy)")[:255],
        description=source.description,
        owner_id=user.id,
        organisation_id=source.organisation_id,
        tags=list(source.tags or []),
        template_key=source.template_key,
    )
    db.add(copy)
    db.flush()

    versions = db.scalars(
        select(GeometryVersion)
        .where(GeometryVersion.project_id == source.id)
        .order_by(GeometryVersion.version_number)
    ).all()
    for version in versions:
        # A new `Media` row on the same blob: the file is shared, the row is not, so deleting
        # either project leaves the other's geometry readable.
        media = _clone_media(db, version, user)
        db.add(
            GeometryVersion(
                project_id=copy.id,
                media_id=media.id,
                version_number=version.version_number,
                filename=version.filename,
                file_format=version.file_format,
                note=version.note,
                stats=dict(version.stats or {}),
            )
        )

    documents = db.scalars(
        select(DesignDocument)
        .where(DesignDocument.project_id == source.id)
        .order_by(DesignDocument.created_at)
    ).all()
    for document in documents:
        conversation = Conversation(
            owner_id=user.id,
            project_id=copy.id,
            title=f"{document.name} (copied design)"[:255],
        )
        db.add(conversation)
        db.flush()
        designs.save(
            db,
            conversation,
            designs.spec_of(document),
            summary=f"Copied from {source.name!r}, revision {document.revision_number}.",
            author=designs.AUTHOR_USER,
            author_id=user.id,
        )
    db.flush()
    return Duplicated(project=copy, geometry_versions=len(versions), designs=len(documents))


def _clone_media(db: Session, version: GeometryVersion, user: User) -> Media:
    original = version.media
    media = Media(
        owner_id=user.id,
        kind=original.kind,
        filename=original.filename,
        content_type=original.content_type,
        size_bytes=original.size_bytes,
        sha256=original.sha256,
        meta={**(original.meta or {}), "copied_from": original.id},
    )
    db.add(media)
    db.flush()
    return media

