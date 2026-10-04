"""AI: one row per agent turn -- cost, steps, cache share, wall time, stop reason

Revision ID: be18af3e6a04
Revises: 90b05c4b90e3

A new table, `turn_metrics` (ROAD_TO_10 0.2). Until now the numbers that decide
whether the agent is affordable existed only as log lines, which cannot be summed or
compared between two settings. Append-only: nothing in the application updates a row.
The conversation key is SET NULL, so a row outlives the conversation it measured;
the user key CASCADEs, because a metric row about a deleted account is personal data
with no remaining purpose.

**Rollback note.** `downgrade` drops the table, and with it every recorded turn
metric. Nothing a user sees depends on it -- the token ledger (`ai_token_usage`), the
bill (`usage_records`) and the transcript are separate tables and are untouched -- but
the baseline every Phase 1 optimisation is judged against is gone, and cannot be
reconstructed from logs. Export it first if a before-and-after is still owed.
Create Date: 2026-10-04 20:35:17.113373

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'be18af3e6a04'
down_revision: Union[str, Sequence[str], None] = '90b05c4b90e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('turn_metrics',
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=True),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('rounds', sa.Integer(), nullable=False),
    sa.Column('step_budget', sa.Integer(), nullable=False),
    sa.Column('model_calls', sa.Integer(), nullable=False),
    sa.Column('tools_offered', sa.Integer(), nullable=False),
    sa.Column('tool_calls', sa.Integer(), nullable=False),
    sa.Column('tool_calls_failed', sa.Integer(), nullable=False),
    sa.Column('tool_calls_blocked', sa.Integer(), nullable=False),
    sa.Column('prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('cached_prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('completion_tokens', sa.Integer(), nullable=False),
    sa.Column('peak_prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_micro_usd', sa.BigInteger(), nullable=True),
    sa.Column('wall_ms', sa.Integer(), nullable=False),
    sa.Column('stop_reason', sa.String(length=32), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_turn_metrics_conversation_created', 'turn_metrics', ['conversation_id', 'created_at'], unique=False)
    op.create_index('ix_turn_metrics_model_created', 'turn_metrics', ['model', 'created_at'], unique=False)
    op.create_index(op.f('ix_turn_metrics_user_id'), 'turn_metrics', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_turn_metrics_user_id'), table_name='turn_metrics')
    op.drop_index('ix_turn_metrics_model_created', table_name='turn_metrics')
    op.drop_index('ix_turn_metrics_conversation_created', table_name='turn_metrics')
    op.drop_table('turn_metrics')
