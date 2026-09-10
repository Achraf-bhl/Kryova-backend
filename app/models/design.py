"""The design record: a `DesignSpec` that outlives the process that built it.

**Why this exists.** `app.design.spec.DesignSpec` has been the compilation target
since E1 and has never been stored anywhere. It lives for the duration of one
call, is compiled, is executed, and is dropped — so the artefact the whole
package was built to give the design ("meaningful diffs, deterministic replay,
version control on a design, because a design is text") could not be shown to
anybody, edited by anybody, or pointed at by an approval gate. P5 task 3 asks for
the spec rendered beside the chat with its parameters editable; P5 task 6 asks
for a parameter edited mid-mission. Both were blocked on this table and said so.

**One document per conversation, and revisions are append-only.** The pairing
matches the one already enforced for CATIA: a conversation owns at most one
document (`CatiaDocument.conversation_id` is unique) and this is the declarative
half of the same thing. A design that could belong to two conversations would
make "the part we are talking about" ambiguous in the one place the agent
resolves it without asking.

**The revision chain is the version control, and it is never rewritten.**
`DesignRevision` holds every spec the document has ever been, with the digest it
had and a sentence about what moved. That is what makes a diff between any two
points answerable after the fact, which is what an approval gate needs when
somebody asks six weeks later what exactly was signed off. Storing only the
current spec would make the gate's `subject_digest` a hash of something nobody
can produce again.

**The digest is `DesignSpec.digest()` and is not recomputed here.** One
implementation of "are these the same design", in the module that owns the
canonical serialisation. A second one on the way into the database would drift
the first time a field was added, and the two disagreeing is exactly the failure
`subject_digest` exists to catch.
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKey
from app.models.types import JSONB_compat as JSONB

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.project import Project
    from app.models.user import User


class DesignDocument(UUIDPrimaryKey, TimestampMixin, Base):
    """The current spec for one conversation, plus where it came from.

    `document` is `DesignSpec.to_dict()` verbatim — the canonical form, which
    carries its own `format_version` and refuses to load into a build that reads
    a different one. Storing the dict rather than a set of columns is
    deliberate: the spec's shape is owned by `app/design/spec.py`, and mirroring
    it into columns would create a second schema that goes stale silently, which
    is the defect the frontend's `types/api.ts` already has and does not need a
    second instance of.
    """

    __tablename__ = "design_documents"
    __table_args__ = (
        # One design per conversation. A unique index rather than a convention,
        # for the same reason `CatiaDocument` has one.
        UniqueConstraint("conversation_id", name="uq_design_document_conversation"),
        Index("ix_design_documents_project", "project_id"),
    )

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    #: Denormalised from the conversation so a project's designs can be listed
    #: without a join through every conversation the project owns. Nullable
    #: because a conversation can start before a project is chosen.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), default=None
    )

    #: `DesignSpec.name` — the part name, and the CATIA document name it becomes.
    #: Duplicated out of `document` because it is what a list view shows, and a
    #: list that has to deserialise every spec to render a name is a list that
    #: gets slow exactly when a user has enough designs to need it.
    name: Mapped[str] = mapped_column(String(255))

    #: `DesignSpec.digest()` of `document`. The identity of this design version.
    digest: Mapped[str] = mapped_column(String(64), index=True)

    #: Monotonic, starting at 1. The head of the revision chain.
    revision_number: Mapped[int] = mapped_column(Integer, default=1)

    document: Mapped[dict[str, Any]] = mapped_column(JSONB)

    conversation: Mapped["Conversation"] = relationship()
    project: Mapped["Project | None"] = relationship()
    revisions: Mapped[list["DesignRevision"]] = relationship(
        back_populates="design",
        cascade="all, delete-orphan",
        order_by="DesignRevision.revision_number",
    )


class DesignRevision(UUIDPrimaryKey, TimestampMixin, Base):
    """One spec this document has been. Written once, never updated.

    `summary` is a sentence about what moved, taken from `app.design.diff` when
    the revision was written rather than computed on read. Computing it on read
    would mean recompiling both specs to render a list, and would give a
    different answer after an operation registry change — a history that
    rewrites itself is not a history.
    """

    __tablename__ = "design_revisions"
    __table_args__ = (
        UniqueConstraint("design_id", "revision_number", name="uq_design_revision_number"),
        Index("ix_design_revisions_design_created", "design_id", "created_at"),
    )

    design_id: Mapped[str] = mapped_column(
        ForeignKey("design_documents.id", ondelete="CASCADE"), index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64), index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB)

    #: What changed to produce this revision, in one line. Empty on revision 1,
    #: which changed nothing — it *is* the beginning.
    summary: Mapped[str] = mapped_column(Text, default="")

    #: Who or what wrote it: a user id for a hand edit, null for the agent.
    #: Null is not "unknown" — `author` says which, and conflating the two would
    #: make "did a person do this" unanswerable, which is the question an audit
    #: of a signed-off design is entirely about.
    author_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    #: `"user"` or `"agent"`. Short and closed rather than free text.
    author: Mapped[str] = mapped_column(String(16), default="agent")

    design: Mapped["DesignDocument"] = relationship(back_populates="revisions")
    author_user: Mapped["User | None"] = relationship()
