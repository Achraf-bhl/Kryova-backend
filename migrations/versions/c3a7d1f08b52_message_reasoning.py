"""AI: a reasoning model's chain of thought, kept with the message it belongs to

Revision ID: c3a7d1f08b52
Revises: 9947d5ff2340

One nullable TEXT column on `conversation_messages`. A provider that returns its
reasoning beside the answer and *requires it back* with the transcript (DeepSeek
rejects a tool-calling request whose earlier assistant turns lack their
`reasoning_content`) needs it kept between agent steps, and the transcript is
rebuilt from this table on every step. Null is what every existing row means --
no reasoning was kept -- so nothing is backfilled and no row changes character.

NULL and the empty string are different on purpose: NULL is "none was kept" (the
provider needs none, thinking was off, or the agent wrote the message itself) and
`''` is "the model returned the field, empty". The column is never part of any
API response; it is the provider's, not the user's.

**Rollback note.** `downgrade` drops the column. Every stored chain of thought is
lost. Nothing the user sees depends on it -- messages, tool calls and results are
untouched -- but a conversation continued afterwards runs its next steps with
thinking off until the older turns leave the replay window, because a provider
that requires the reasoning back cannot be sent turns that no longer have it.
Export the column first only if the reasoning itself matters to anyone.
Create Date: 2026-10-04 15:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3a7d1f08b52'
down_revision: Union[str, Sequence[str], None] = '9947d5ff2340'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'conversation_messages',
        sa.Column('reasoning', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('conversation_messages', 'reasoning')
