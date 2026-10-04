"""AI ledger: cached prompt tokens, and what each call cost

Revision ID: 90b05c4b90e3
Revises: c3a7d1f08b52

Two columns on `ai_token_usage` (ROAD_TO_10 1.1 and 1.2).

`cached_prompt_tokens` is the part of `prompt_tokens` the vendor served from its
prompt cache -- a *subset*, never added to it -- and it is NOT NULL with a server
default of 0, because every row written before today genuinely has none recorded.
That 0 means "not recorded", which prices as a miss: the safe direction to be wrong
in, since a cache read costs less than a fresh token and never more.

`cost_micro_usd` is the call's price in micro-dollars, computed from the
configuration in force when the call was made. Nullable, and NULL is deliberate:
no price was configured for the model, which is not the same as the call being
free. Existing rows stay NULL -- they were never priced and are not back-filled,
because pricing history with today's price list would put a number in the ledger
nobody ever computed at the time.

**Rollback note.** `downgrade` drops both columns. Every recorded cache split and
every priced cost is lost; token counts, the budget and the bill (`usage_records`)
are untouched, so nothing a user sees changes -- but a cost budget configured
afterwards would read zero spend until calls are priced again. Export the two
columns first only if the cost history matters.
Create Date: 2026-10-04 20:28:57.215430

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '90b05c4b90e3'
down_revision: Union[str, Sequence[str], None] = 'c3a7d1f08b52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('ai_token_usage', sa.Column('cached_prompt_tokens', sa.Integer(), server_default='0', nullable=False))
    op.add_column('ai_token_usage', sa.Column('cost_micro_usd', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ai_token_usage', 'cost_micro_usd')
    op.drop_column('ai_token_usage', 'cached_prompt_tokens')
