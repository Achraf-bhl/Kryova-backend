"""Reading, writing and editing the persisted design record (P5 tasks 3 and 6).

The thin layer between `app.design` — which is pure, knows nothing about a
database, and must stay that way — and the two rows in `app.models.design`.
Everything here is about *when* a spec becomes a revision and what the revision
is allowed to say about itself.

**Three rules, and each of them is a defect this would otherwise have.**

1. **A revision that changes nothing is not written.** The agent re-saves the
   spec after every step it takes, and most steps do not touch it. Appending an
   identical spec each time would give a design six hundred revisions, of which
   four matter, and would make "what changed on Tuesday" unanswerable by
   drowning it. `save` compares digests and returns the existing head untouched.

2. **The summary is written at the time, from `app.design.diff`.** Not
   recomputed on read — recomputing means recompiling both specs to render a
   list, and gives a *different answer* after an operation registry change,
   because the diff is made after compilation on purpose. A history that
   rewrites itself when the compiler changes is not a history.

3. **An edit that does not compile is refused before anything is written.**
   `diff_specs` compiles both sides, so a spec that cannot build raises there;
   doing that first means the failure arrives as the compiler's own message,
   which already names the feature and says what to do, rather than as a
   half-written revision chain.

**`set_parameter` refuses a derived parameter, and does not reimplement that
refusal.** `DesignSpec.set_parameter` already says why overwriting a consequence
with a literal is worse than either alternative; this calls it and lets the
`SpecError` through. A second copy of the rule here would be a second place for
it to soften.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.design.diff import SpecDiff, diff_specs
from app.design.spec import DesignSpec
from app.models.conversation import Conversation
from app.models.design import DesignDocument, DesignRevision

#: What `DesignRevision.author` may be. Two values, closed, because the question
#: it answers — did a person do this — has two answers and a third would mean
#: somebody had invented a way of not saying.
AUTHOR_USER = "user"
AUTHOR_AGENT = "agent"


class DesignNotFound(LookupError):
    """No design has been stored for this conversation yet.

    A distinct type rather than `None` at every call site: three of the four
    callers want to turn this into a 404 with the same sentence, and the fourth
    (the agent) wants to create one, so the branch is worth naming.
    """


@dataclass(frozen=True)
class SaveOutcome:
    """What a save did, which is not always what it was asked to do.

    `changed` is false when the incoming spec was byte-identical to the head. It
    is returned rather than inferred from the revision number so a caller can
    say "no change" without having to remember what the number was before.
    """

    document: DesignDocument
    revision: DesignRevision
    changed: bool
    diff: SpecDiff | None = None


def spec_of(document: DesignDocument) -> DesignSpec:
    """The head spec of a document."""
    return DesignSpec.from_dict(document.document)


def spec_of_revision(revision: DesignRevision) -> DesignSpec:
    return DesignSpec.from_dict(revision.document)


def load(db: Session, conversation: Conversation) -> DesignDocument | None:
    """The design this conversation owns, or `None` if it has never had one."""
    return db.scalar(
        select(DesignDocument).where(DesignDocument.conversation_id == conversation.id)
    )


def require(db: Session, conversation: Conversation) -> DesignDocument:
    document = load(db, conversation)
    if document is None:
        raise DesignNotFound(
            "This conversation has no design yet. One appears when the agent first "
            "describes a part as a specification rather than as a sequence of "
            "operations."
        )
    return document


def revision(db: Session, document: DesignDocument, number: int) -> DesignRevision | None:
    return db.scalar(
        select(DesignRevision).where(
            DesignRevision.design_id == document.id,
            DesignRevision.revision_number == number,
        )
    )


def save(
    db: Session,
    conversation: Conversation,
    spec: DesignSpec,
    *,
    summary: str = "",
    author: str = AUTHOR_AGENT,
    author_id: str | None = None,
) -> SaveOutcome:
    """Store `spec` as this conversation's design, if it is not already.

    Creates the document on the first call. On every later call it compares
    digests: an unchanged spec returns the existing head with `changed=False`
    and writes nothing, which is rule 1 above.

    `summary` is used as given when supplied — the agent knows what it just did
    better than a diff does ("thickened the web to clear the bolt head" against
    "web_mm: 6 -> 8"). When it is empty the diff writes one. Revision 1 gets no
    summary at all: it changed nothing, it *is* the beginning.
    """
    document = load(db, conversation)
    document_dict = spec.to_dict()
    digest = spec.digest()

    if document is None:
        document = DesignDocument(
            conversation_id=conversation.id,
            project_id=conversation.project_id,
            name=spec.name,
            digest=digest,
            revision_number=1,
            document=document_dict,
        )
        db.add(document)
        db.flush()
        first = DesignRevision(
            design_id=document.id,
            revision_number=1,
            digest=digest,
            document=document_dict,
            summary="",
            author=author,
            author_id=author_id,
        )
        db.add(first)
        db.flush()
        return SaveOutcome(document=document, revision=first, changed=True, diff=None)

    if document.digest == digest:
        head = revision(db, document, document.revision_number)
        # The head revision is written in the same transaction as the document
        # and cascades with it, so its absence would mean the chain had been
        # broken by something outside this module. Refusing here beats returning
        # a `SaveOutcome` with a null revision that every caller then has to
        # narrow.
        if head is None:  # pragma: no cover - defended, not expected
            raise DesignNotFound(
                f"Design {document.id} claims revision {document.revision_number} "
                "and no such revision exists. The revision chain is broken."
            )
        return SaveOutcome(document=document, revision=head, changed=False, diff=None)

    # Compiles both sides. A spec that does not build raises here, before the
    # revision chain has been touched — rule 3.
    change = diff_specs(spec_of(document), spec)

    document.revision_number += 1
    document.digest = digest
    document.document = document_dict
    document.name = spec.name
    # A conversation can acquire a project after its design was started, and the
    # denormalised column is only useful if it follows.
    document.project_id = conversation.project_id

    entry = DesignRevision(
        design_id=document.id,
        revision_number=document.revision_number,
        digest=digest,
        document=document_dict,
        summary=summary or change.what_changed() or change.summary(),
        author=author,
        author_id=author_id,
    )
    db.add(entry)
    db.flush()
    return SaveOutcome(document=document, revision=entry, changed=True, diff=change)


def set_parameter(
    db: Session,
    document: DesignDocument,
    name: str,
    value: float,
    *,
    author: str = AUTHOR_USER,
    author_id: str | None = None,
) -> SaveOutcome:
    """Change one decision in the design and record what it reaches.

    The edit P5 task 6 calls "edit a parameter mid-mission". It is a revision
    like any other, so a run that is in flight sees it the next time it reads
    the spec, and a reviewer afterwards sees who moved it and what moved with it.

    A parameter that does not exist, or one that is derived, raises `SpecError`
    from `DesignSpec.set_parameter` — the sentence there is better than one
    written here would be, because it lists the parameters that do exist.
    """
    current = spec_of(document)
    edited = current.set_parameter(name, value)
    before = _describe_parameter(current, name)
    conversation = db.get(Conversation, document.conversation_id)
    if conversation is None:  # pragma: no cover - FK-enforced
        raise DesignNotFound(f"Design {document.id} points at a conversation that is gone.")
    return save(
        db,
        conversation,
        edited,
        summary=f"{name}: {before} -> {value:g}",
        author=author,
        author_id=author_id,
    )


def diff_between(
    document: DesignDocument, before: DesignRevision, after: DesignRevision
) -> SpecDiff:
    """What changed between any two points in this document's history.

    Both revisions must belong to `document`. Diffing across two designs would
    produce a shape the type permits and the meaning does not — every feature
    added and every feature removed — and it would look like an answer.
    """
    for candidate in (before, after):
        if candidate.design_id != document.id:
            raise ValueError(
                f"Revision {candidate.revision_number} belongs to a different design. "
                "A diff between two designs is not a diff, it is two lists."
            )
    return diff_specs(spec_of_revision(before), spec_of_revision(after))


def _describe_parameter(spec: DesignSpec, name: str) -> str:
    for parameter in spec.parameters:
        if parameter.name == name:
            if parameter.expression is not None:
                return f"={parameter.expression}"
            return f"{parameter.value:g}" if parameter.value is not None else "unset"
    return "unset"
