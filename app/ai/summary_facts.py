"""The half of a conversation summary the server already knows exactly.

The fold used to be one LLM paraphrase of everything the window dropped. A paraphrase
is the right tool for what only the transcript holds -- "the user prefers aluminium",
"we tried a ribbed web and rejected it for weight" -- and the wrong one for what the
server wrote down at the moment it happened: a parameter's value, who changed it, a
sign-off that was refused and why. A summariser under pressure to be fluent rounds a
number, drops the one it thinks is superseded, or merges two decisions into one, and
nothing afterwards says it did. So the fold now has two parts (ROAD_TO_10 2.1):

* **This module** -- facts read from the design record and the approval gates.
  Deterministic, costs no tokens to produce, and byte-stable: the same rows give the
  same text.
* **The model's note** -- intent only. The summariser is shown these facts, so it does
  not spend its space restating them.

Four rules, each pinned by a test that fails when it is removed:

1. **Frozen at the fold, never read live.** The summary message sits at the front of
   the replayed history, ahead of everything the provider caches. A fact read live
   would change whenever a parameter did, and every change would re-bill every token
   behind it at the full price (`context.py` and `cache_health.py` say why that matters).
   Written once, when the fold happens, it changes only when the boundary already does.
   It can therefore be older than the design; the state block is live and the summary
   preamble says to prefer it.
2. **No clock, no name.** No ages ("3 days ago") and no person's name: the first would
   make the text differ between two folds of the same rows, the second sends a
   colleague's identity to a hosted model for no gain.
3. **Complete where it is a decision, bounded where it is a log.** Every parameter is
   listed (up to a ceiling no real design reaches), because a parameter is exactly what
   a later turn gets wrong; the change log keeps the newest entries and says how many
   were left out.
4. **Not duplicated where the state block already carries it live.** The plan and the
   loose ends of the operation log are in the state block on every turn, and a frozen
   copy beside a live one would only ever disagree with it.

Never raises: a fold that cannot read the record falls back to the model's note alone.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.sanitise import sanitise_untrusted
from app.models import Conversation
from app.models.design import DesignDocument, DesignRevision
from app.models.gates import ApprovalGate, GateState

logger = logging.getLogger(__name__)

#: A design with more parameters than this is a catalogue, not a part. The ceiling is a
#: backstop against an unbounded summary, not a policy a real design meets.
MAX_PARAMETERS = 80
#: The newest design changes kept. Older ones are counted, and `read_design` has them.
MAX_CHANGES = 30
#: Decided sign-offs kept, newest last.
MAX_SIGNOFFS = 20
#: Per-line cap on anything a person or a model typed.
MAX_LINE_CHARS = 200

HEADER = (
    "Recorded by the server, exactly as it was written at the time of the fold (not "
    "paraphrased; the live design is in the state block):"
)


def _clean(text: str) -> str:
    return sanitise_untrusted(" ".join(str(text).split()), max_chars=MAX_LINE_CHARS)


def _parameters(document: DesignDocument) -> list[str]:
    from app.core import designs

    spec = designs.spec_of(document)
    shown = list(spec.parameters)[:MAX_PARAMETERS]
    rendered = [
        f"{_clean(p.name)}="
        f"{_clean(p.expression) if p.expression is not None else f'{p.value:g}'}"
        f"{(' ' + p.unit.value) if p.unit.value else ''}"
        for p in shown
    ]
    if len(spec.parameters) > len(shown):
        rendered.append(f"and {len(spec.parameters) - len(shown)} more (read_design lists every one)")
    return rendered


def _design_lines(db: Session, conversation: Conversation) -> list[str]:
    from app.core import designs

    document = designs.load(db, conversation)
    if document is None:
        return []

    lines: list[str] = []
    try:
        names = _parameters(document)
    except Exception:  # noqa: BLE001 - a spec this build cannot read is the state block's to report
        names = []
    if names:
        lines.append(
            f"design parameters (revision {document.revision_number}): " + ", ".join(names)
        )

    newest = list(
        db.scalars(
            select(DesignRevision)
            .where(DesignRevision.design_id == document.id, DesignRevision.revision_number > 1)
            .order_by(DesignRevision.revision_number.desc())
            .limit(MAX_CHANGES)
        )
    )
    newest.reverse()
    earlier = max(0, document.revision_number - 1 - len(newest))
    if newest:
        lines.append("design changes, oldest first:")
        if earlier:
            lines.append(f"  ({earlier} earlier change(s) not listed; read_design has them)")
        for row in newest:
            lines.append(f"  revision {row.revision_number} ({row.author}): {_clean(row.summary)}")
    return lines


def _signoff_lines(db: Session, conversation: Conversation) -> list[str]:
    rows = list(
        db.scalars(
            select(ApprovalGate)
            .where(
                ApprovalGate.conversation_id == conversation.id,
                ApprovalGate.state.in_([GateState.APPROVED, GateState.REJECTED]),
            )
            .order_by(ApprovalGate.decided_at.desc(), ApprovalGate.id.desc())
            .limit(MAX_SIGNOFFS)
        )
    )
    if not rows:
        return []
    rows.reverse()
    lines = ["sign-offs, oldest first:"]
    for gate in rows:
        state = gate.state.value if isinstance(gate.state, GateState) else str(gate.state)
        note = f" -- {_clean(gate.decision_note)}" if gate.decision_note else ""
        lines.append(f'  "{_clean(gate.title)}": {state}{note}')
    return lines


def build(db: Session, conversation: Conversation) -> str:
    """The server's own account of the decisions in this conversation, or `""`.

    `""` for a conversation with no design and no sign-off, which is most of them: the
    summary message is then byte-identical to what it was before this existed.
    """
    if not conversation.id:
        return ""
    try:
        lines = [*_design_lines(db, conversation), *_signoff_lines(db, conversation)]
    except Exception:  # noqa: BLE001 - the fold must survive a record it cannot read
        logger.warning(
            "Could not read the design record for conversation %s; the fold will carry "
            "the model's note alone",
            conversation.id,
            exc_info=True,
        )
        return ""
    if not lines:
        return ""
    return "\n".join([HEADER, *lines])
