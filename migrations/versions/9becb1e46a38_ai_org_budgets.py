"""ai org budgets: organisation on the ledger, alert claims, tenant caps

Revision ID: 9becb1e46a38
Revises: be18af3e6a04
Create Date: 2026-10-04 20:49:37.397226

ROAD_TO_10 1.3.

* `ai_token_usage.organisation_id` (nullable, SET NULL) with an
  `(organisation_id, usage_date)` index -- the organisation's cap is the user's
  daily lookup summed over its members. **Existing rows are not back-filled**, on
  purpose: the cap sums `cost_micro_usd`, which is NULL on every row written
  before migration 90b05c4b90e3, so an old row would add nothing to it anyway, and
  guessing a tenant for historic calls would put a number in a ledger nobody
  computed at the time.
* `ai_budget_alerts` -- one row per (organisation, period, period start,
  threshold); the unique constraint is what makes "warn once" safe under two
  turns finishing together.
* `billing_accounts.ai_org_{daily,monthly}_cost_budget_micro_usd` -- per-tenant
  overrides. Null means the global setting; 0 means this tenant is unlimited.

**Rollback note.** `downgrade` drops all of it. The alert history is lost (so a
re-upgrade could re-send a warning for a period already warned about), every
ledger row forgets which organisation it was billed to, and any per-tenant cap
configured through the billing endpoint reverts to the global setting. Token
counts, per-call costs and the bill (`usage_records`) are untouched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9becb1e46a38'
down_revision: Union[str, Sequence[str], None] = 'be18af3e6a04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ai_budget_alerts',
    sa.Column('organisation_id', sa.String(length=36), nullable=False),
    sa.Column('period', sa.String(length=8), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('threshold', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organisation_id', 'period', 'period_start', 'threshold', name='uq_ai_budget_alert_once')
    )
    op.create_index(op.f('ix_ai_budget_alerts_organisation_id'), 'ai_budget_alerts', ['organisation_id'], unique=False)
    op.add_column('ai_token_usage', sa.Column('organisation_id', sa.String(length=36), nullable=True))
    op.create_index('ix_ai_token_usage_org_day', 'ai_token_usage', ['organisation_id', 'usage_date'], unique=False)
    op.create_foreign_key('ai_token_usage_organisation_id_fkey', 'ai_token_usage', 'organisations', ['organisation_id'], ['id'], ondelete='SET NULL')
    op.add_column('billing_accounts', sa.Column('ai_org_daily_cost_budget_micro_usd', sa.BigInteger(), nullable=True))
    op.add_column('billing_accounts', sa.Column('ai_org_monthly_cost_budget_micro_usd', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('billing_accounts', 'ai_org_monthly_cost_budget_micro_usd')
    op.drop_column('billing_accounts', 'ai_org_daily_cost_budget_micro_usd')
    op.drop_constraint('ai_token_usage_organisation_id_fkey', 'ai_token_usage', type_='foreignkey')
    op.drop_index('ix_ai_token_usage_org_day', table_name='ai_token_usage')
    op.drop_column('ai_token_usage', 'organisation_id')
    op.drop_index(op.f('ix_ai_budget_alerts_organisation_id'), table_name='ai_budget_alerts')
    op.drop_table('ai_budget_alerts')
