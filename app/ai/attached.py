"""What the user attached, put in front of the agent (master plan P4.7).

Until this module existed, `app/documents/` could read a spreadsheet down to the
cell and nothing carried a single character of it into a turn. `list_for` fed
the panel, `quote_for_user_turn` was called by nobody outside its own package,
and the agent learned about an attachment only from the user's own words. So the
phase proof — *a load-case spreadsheet becomes a named, provenance-tagged load
case* — described a path the product did not have.

Three decisions are taken here, and each is a trade-off rather than an obvious
answer.

**Content is quoted on the turn after it was attached; the inventory goes every
turn.** Quoting everything every turn is the simple rule and it is unaffordable:
`MAX_TURN_CHARS` is 12,000 characters, and on a local model prompt
*re-processing* dominates a turn (CLAUDE.md, testing item 11 — 3.7–7.9 tok/s,
measured), so re-sending a datasheet on every step would cost minutes per step
and grow with the transcript. Quoting once and never again is the other simple
rule, and it is wrong for the reason `app/ai/resume.py` exists: the window trims
and the summary is a paraphrase, so "once" eventually means "never". What is
sent every turn is `inventory_line` — a few dozen characters naming the file,
its status and its fragment count — which keeps the file *knowable* while the
content itself is recoverable through `read_attachment`.

**"Since the user last spoke" is the rule for new, not a stored flag.** An
attachment is quoted when it was created after the most recent user message in
its conversation. That needs no column and no migration, it is exactly the
question worth asking (*what has the user handed me since they last said
something*), and it fails safe: a tie in timestamps quotes again, and quoting
twice costs context while never quoting loses the file.

**A `FAILED` or `UNSUPPORTED` attachment is named, with its reason.** It has no
content to quote, so the temptation is to leave it out of a block that is about
content. But the agent that says nothing about the PDF the user dropped in reads
as the product having ignored it — and the reason is already server-authored
prose naming the next action (`status_detail`), which is precisely what the
agent needs in order to tell the user what to do instead.

Ownership is `list_for`'s, which is owner-scoped: an attachment another member
made in a shared conversation is not quoted into this user's turn, and
`read_attachment` refuses it as not found.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import attachments as attachment_store
from app.documents.quoted import UserTurnBlock, quote_for_user_turn
from app.models import Conversation, User
from app.models.attachment import Attachment
from app.models.conversation import ConversationMessage, MessageRole

#: How many attachments are described in one turn's inventory. A conversation
#: someone has dropped forty files into still gets a bounded block; the rest are
#: counted in one line rather than listed, and `read_attachment` reaches any of
#: them by id.
MAX_LISTED = 20


@dataclass(frozen=True)
class TurnAttachments:
    """The block for this turn, and what went into it.

    `block` is the only part that reaches a model, and it can only reach one as
    a user turn. The counts are for the caller's own telemetry and tests, and
    carry no payload characters — deliberately, so that logging this object can
    never leak a document.
    """

    block: UserTurnBlock
    listed: int
    quoted: int

    def __bool__(self) -> bool:
        return bool(self.block)


def for_turn(db: Session, conversation: Conversation, owner: User) -> TurnAttachments:
    """Everything this conversation's attachments contribute to one user turn.

    Call it *before* the user's message is appended: `since_last_user_message`
    reads the previous turn's timestamp, and appending first would make every
    attachment older than the message that has just arrived, so nothing would
    ever be quoted.
    """
    rows = list(attachment_store.list_for(db, conversation=conversation, owner=owner))
    if not rows:
        return TurnAttachments(block=quote_for_user_turn(()), listed=0, quoted=0)

    shown = rows[:MAX_LISTED]
    notes = [attachment_store.inventory_line(row) for row in shown]
    if len(rows) > len(shown):
        notes.append(
            f"- and {len(rows) - len(shown)} older attachment(s), not listed. "
            "read_attachment reaches any of them by id."
        )

    cutoff = since_last_user_message(db, conversation)
    fresh = [row for row in rows if _is_new(row, cutoff)]
    items = [text for row in fresh for text in attachment_store.stored_fragments(row)]
    if fresh and not items:
        notes.append(
            "- nothing was quoted below: the attachments above hold no readable text. "
            "Tell the user what each was read as rather than describing its contents."
        )
    elif not fresh:
        notes.append(
            "- no attachment is new since your last turn, so nothing is quoted below. "
            "Call read_attachment to read one again."
        )
    else:
        notes.append(
            "- read_attachment returns any fragment of any attachment above, including "
            "the ones this turn's budget left out."
        )

    return TurnAttachments(
        block=quote_for_user_turn(items, notes=notes),
        listed=len(rows),
        quoted=len(items),
    )


def since_last_user_message(db: Session, conversation: Conversation) -> datetime | None:
    """When the user last spoke in this conversation, or `None` if never.

    `None` on the first turn, which makes every attachment new — correct, and
    the reason the comparison below treats `None` as "quote it".
    """
    return db.scalar(
        select(func.max(ConversationMessage.created_at)).where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.role == MessageRole.USER,
        )
    )


def _is_new(attachment: Attachment, cutoff: datetime | None) -> bool:
    """Was this attached since the user last spoke?

    A missing timestamp on either side counts as new. Both are set by the
    database, so neither is expected to be missing — and if one is, quoting a
    file twice is a cost and not quoting it at all is a defect.
    """
    if cutoff is None or attachment.created_at is None:
        return True
    return attachment.created_at >= cutoff
