"""plan limits on the billing account

Revision ID: 16c0cc4190d5
Revises: 7da3113b27aa
Create Date: 2026-10-05 01:09:09.796263

ROAD_TO_10 3.5.

Five nullable integer columns on `billing_accounts`: the length of the queue behind the
concurrent-run limit (`max_waiting_simulations_per_user`) and the per-minute budgets for
chat, simulations, MCP and CATIA operations. Each is a *per-tenant override* -- null means
"the plan's limit, then the global setting of the same name", which is every existing row's
behaviour before this revision, so **no backfill is needed and nothing changes for an
organisation until an owner writes one**.

`0` is stored for `max_waiting_simulations_per_user` only (nothing waits); the API refuses 0
for the four rates, because it would read as both "unlimited" and "nothing allowed".

**Rollback note.** `downgrade` drops the five columns, so every per-tenant rate or queue
override an owner set is lost and those organisations fall back to their plan's or the global
limit -- stricter or looser depending on what they had been given. Nothing else reads the
columns once the code is rolled back with it; rolling back the schema without the code makes
the billing routes fail on the missing attributes, so roll them back together.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '16c0cc4190d5'
down_revision: Union[str, Sequence[str], None] = '7da3113b27aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('billing_accounts', sa.Column('max_waiting_simulations_per_user', sa.Integer(), nullable=True))
    op.add_column('billing_accounts', sa.Column('chat_requests_per_minute', sa.Integer(), nullable=True))
    op.add_column('billing_accounts', sa.Column('simulation_requests_per_minute', sa.Integer(), nullable=True))
    op.add_column('billing_accounts', sa.Column('mcp_requests_per_minute', sa.Integer(), nullable=True))
    op.add_column('billing_accounts', sa.Column('catia_ops_per_minute', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('billing_accounts', 'catia_ops_per_minute')
    op.drop_column('billing_accounts', 'mcp_requests_per_minute')
    op.drop_column('billing_accounts', 'simulation_requests_per_minute')
    op.drop_column('billing_accounts', 'chat_requests_per_minute')
    op.drop_column('billing_accounts', 'max_waiting_simulations_per_user')
