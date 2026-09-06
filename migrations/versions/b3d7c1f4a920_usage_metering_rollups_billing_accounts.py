"""usage metering, rollups, billing accounts and metering faults

Revision ID: b3d7c1f4a920
Revises: 2f3f8aadb319
Create Date: 2026-09-06

Phase P8. Four tables and one piece of DDL `create_table` cannot express.

**Three of the four are tenant tables and get the P2 row-level-security
policy.** `usage_records`, `usage_rollups` and `billing_accounts` all carry
`organisation_id` directly, so they take the same `_predicate` the organisations
migration wrote for `projects` -- read the tenant list out of
`kryova.organisation_ids`, and stand aside when no tenant context has been
established. `FORCE ROW LEVEL SECURITY` is not optional for the same reason it
was not there: Postgres exempts a table's owner from its own policies unless it
is forced, and the application currently connects as the owning role on Neon
(`app/core/database.py::connected_role_bypasses_rls` records that caveat in
full, and it applies here unchanged).

**`metering_faults` is the fourth, and its `organisation_id` deliberately has no
foreign key.** The most likely cause of a metering failure is a missing or
invalid tenant id, and a foreign key would make recording *that* fact fail for
exactly the reason the original write failed. It still gets the policy, so a
fault row belonging to one tenant is not readable by another; a fault with no
tenant at all is readable only outside a tenant context, which is where the
operator looking for it already is.

The SQL below is a copy of the predicate in
`1b07f4f27e89_organisations_memberships_invitations_.py`, not an import of it,
following the precedent both earlier migrations set: a migration has to keep
meaning the same thing years after the application module has moved.
`tests/test_billing_tenancy.py` asserts the GUC name here matches
`app.core.database.TENANT_SETTING`, because a typo is a policy that never
matches and therefore never protects.

Rollback note: `downgrade()` drops the policies and then the four tables. It
destroys the usage ledger, which is a billing record -- take a dump of
`usage_records` and `usage_rollups` before running it in any environment that
has ever billed anybody.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3d7c1f4a920'
down_revision: Union[str, Sequence[str], None] = '2f3f8aadb319'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Must match `app.core.database.TENANT_SETTING`, as the P2 and P3 copies do.
TENANT_SETTING = "kryova.organisation_ids"

_TENANTS = f"string_to_array(nullif(current_setting('{TENANT_SETTING}', true), ''), ',')"

#: Every table below owns its tenant column directly.
_TENANT_TABLES: list[str] = [
    "billing_accounts",
    "usage_records",
    "usage_rollups",
    "metering_faults",
]


def _predicate(column: str = "organisation_id") -> str:
    return f"({_TENANTS} IS NULL OR {column} = ANY({_TENANTS}))"


def rls_statements(schema: str) -> list[str]:
    """The RLS DDL for a given schema, as executable SQL.

    A function rather than inlined so an isolation test can apply *this* DDL to
    its own schema instead of a hand-copied approximation of it -- the same
    reasoning the P2 migration records.
    """
    statements: list[str] = []
    for table in _TENANT_TABLES:
        qualified = f'"{schema}"."{table}"'
        predicate = _predicate()
        statements += [
            f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {qualified}",
            f"CREATE POLICY {table}_tenant_isolation ON {qualified} "
            f"USING {predicate} WITH CHECK {predicate}",
        ]
    return statements


def rls_teardown_statements(schema: str) -> list[str]:
    statements: list[str] = []
    for table in _TENANT_TABLES:
        qualified = f'"{schema}"."{table}"'
        statements += [
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {qualified}",
            f"ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {qualified} DISABLE ROW LEVEL SECURITY",
        ]
    return statements


def _schema() -> str:
    """The schema this migration is running against.

    `migrations/env.py` sets `search_path` on the direct (non-pooler) endpoint
    before Alembic runs, so `current_schema()` is the application's schema.
    Reading it back beats importing application settings -- the same reasoning
    the P2 and P3 migrations record.
    """
    return op.get_bind().scalar(text("SELECT current_schema()")) or "public"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'billing_accounts',
        sa.Column('organisation_id', sa.String(length=36), nullable=False),
        sa.Column('plan', sa.String(length=16), nullable=False),
        sa.Column('credit_balance_minor', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('external_provider', sa.String(length=32), nullable=True),
        sa.Column('external_customer_ref', sa.String(length=128), nullable=True),
        sa.Column('max_concurrent_simulations_per_user', sa.Integer(), nullable=True),
        sa.Column('max_media_bytes', sa.BigInteger(), nullable=True),
        sa.Column('ai_daily_token_budget', sa.BigInteger(), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organisation_id', name='uq_billing_account_organisation'),
    )
    op.create_index(
        op.f('ix_billing_accounts_organisation_id'),
        'billing_accounts',
        ['organisation_id'],
        unique=False,
    )

    op.create_table(
        'usage_rollups',
        sa.Column('organisation_id', sa.String(length=36), nullable=False),
        sa.Column('meter', sa.String(length=32), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('quantity_units', sa.BigInteger(), nullable=False),
        sa.Column('record_count', sa.Integer(), nullable=False),
        sa.Column('sealed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('external_ref', sa.String(length=128), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'organisation_id',
            'meter',
            'period_start',
            'period_end',
            name='uq_usage_rollup_period',
        ),
    )
    op.create_index(
        op.f('ix_usage_rollups_organisation_id'),
        'usage_rollups',
        ['organisation_id'],
        unique=False,
    )

    op.create_table(
        'usage_records',
        sa.Column('organisation_id', sa.String(length=36), nullable=False),
        sa.Column('meter', sa.String(length=32), nullable=False),
        sa.Column('quantity_units', sa.BigInteger(), nullable=False),
        sa.Column('basis', sa.String(length=16), nullable=False),
        sa.Column('method', sa.String(length=300), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('usage_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('subject_type', sa.String(length=32), nullable=False),
        sa.Column('subject_id', sa.String(length=64), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('simulation_job_id', sa.String(length=36), nullable=True),
        sa.Column('geometry_version_id', sa.String(length=36), nullable=True),
        sa.Column('conversation_id', sa.String(length=36), nullable=True),
        sa.Column('media_id', sa.String(length=36), nullable=True),
        sa.Column('user_id', sa.String(length=36), nullable=True),
        sa.Column(
            'detail',
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'),
            nullable=False,
        ),
        sa.Column('rollup_id', sa.String(length=36), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        # Every one of these is SET NULL, never CASCADE: deleting a project must
        # not delete the record that its work was billed. The denormalised
        # `subject_type`/`subject_id` above are what still name the cause once a
        # subject is gone.
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['geometry_version_id'], ['geometry_versions.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(['media_id'], ['media.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['rollup_id'], ['usage_rollups.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['simulation_job_id'], ['simulation_jobs.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_usage_records_org_date_meter',
        'usage_records',
        ['organisation_id', 'usage_date', 'meter'],
        unique=False,
    )
    op.create_index(
        'ix_usage_records_subject', 'usage_records', ['subject_type', 'subject_id'], unique=False
    )
    op.create_index(
        op.f('ix_usage_records_occurred_at'), 'usage_records', ['occurred_at'], unique=False
    )
    op.create_index(
        op.f('ix_usage_records_organisation_id'),
        'usage_records',
        ['organisation_id'],
        unique=False,
    )
    op.create_index(op.f('ix_usage_records_project_id'), 'usage_records', ['project_id'], unique=False)
    op.create_index(op.f('ix_usage_records_rollup_id'), 'usage_records', ['rollup_id'], unique=False)

    op.create_table(
        'metering_faults',
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('meter', sa.String(length=32), nullable=True),
        # No foreign key, on purpose -- see the module docstring.
        sa.Column('organisation_id', sa.String(length=36), nullable=True),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('failure', sa.Text(), nullable=False),
        sa.Column(
            'detail',
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'),
            nullable=False,
        ),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_metering_faults_occurred_at'), 'metering_faults', ['occurred_at'], unique=False
    )

    for statement in rls_statements(_schema()):
        op.execute(statement)


def downgrade() -> None:
    """Downgrade schema. Destroys the usage ledger -- see the module docstring."""
    for statement in rls_teardown_statements(_schema()):
        op.execute(statement)

    op.drop_index(op.f('ix_metering_faults_occurred_at'), table_name='metering_faults')
    op.drop_table('metering_faults')
    op.drop_index(op.f('ix_usage_records_rollup_id'), table_name='usage_records')
    op.drop_index(op.f('ix_usage_records_project_id'), table_name='usage_records')
    op.drop_index(op.f('ix_usage_records_organisation_id'), table_name='usage_records')
    op.drop_index(op.f('ix_usage_records_occurred_at'), table_name='usage_records')
    op.drop_index('ix_usage_records_subject', table_name='usage_records')
    op.drop_index('ix_usage_records_org_date_meter', table_name='usage_records')
    op.drop_table('usage_records')
    op.drop_index(op.f('ix_usage_rollups_organisation_id'), table_name='usage_rollups')
    op.drop_table('usage_rollups')
    op.drop_index(op.f('ix_billing_accounts_organisation_id'), table_name='billing_accounts')
    op.drop_table('billing_accounts')
