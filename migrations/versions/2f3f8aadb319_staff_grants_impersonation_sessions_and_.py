"""staff grants, impersonation sessions and the append-only audit log

Revision ID: 2f3f8aadb319
Revises: 1b07f4f27e89
Create Date: 2026-09-06 08:30:40.943272

Phase P3. Three tables, and two pieces of DDL that no `create_table` call can
express -- the second of which is the point of the whole phase.

**`audit_events` is made append-only by a trigger, not by a convention.** A
`BEFORE UPDATE OR DELETE` trigger raises on every attempt, and a second,
statement-level trigger does the same for `TRUNCATE` -- which fires no row
triggers and would otherwise empty the table in one statement the first rule
never sees. `REVOKE UPDATE, DELETE, TRUNCATE ... FROM PUBLIC` is the belt to
that trigger's braces: it is what still stands if somebody drops the trigger,
for every role except the table's owner.

What this does **not** claim: the owning role can `ALTER TABLE ... DISABLE
TRIGGER` and then do as it likes, and on Neon the application currently
connects as that owner (`connected_role_bypasses_rls` in
`app/core/database.py` records the same honest caveat about RLS). That is
exactly why every entry also carries the previous entry's hash: a row removed
by somebody with the power to remove the guard still leaves a break that
`verify_chain` finds. The trigger prevents; the chain detects; neither is a
substitute for the other, and saying which does which is the honest claim.

**The RLS policy on `audit_events` reads a second GUC.** `kryova.staff` is
published by `require_staff` with `SET LOCAL`, exactly as `tenant_scope`
publishes the tenant list and for exactly the same pooled-endpoint reason. It
is what lets the operations console read the whole log while an organisation
owner reading their own slice stays confined to their tenant by the database
as well as by the route.

`WITH CHECK (true)` on that policy is deliberate, and is the one asymmetry
here. Appending to the audit log must never be refusable by tenant context: a
log that can be blocked by putting a request in the wrong tenant is a log that
can be suppressed. Reads are constrained; writes are constrained by the
trigger above and by there being exactly one writer (`AuditService`).

The SQL below is a copy of `app.models.audit.append_only_statements`, not an
import of it, following the precedent the P2 migration set: a migration has to
keep meaning the same thing years after the application module has moved.
`tests/test_audit.py` asserts the two agree statement for statement, so the
copy cannot drift in silence.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2f3f8aadb319'
down_revision: Union[str, Sequence[str], None] = '1b07f4f27e89'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Must match `app.core.audit.STAFF_SETTING`; `tests/test_audit.py` asserts it.
STAFF_SETTING = "kryova.staff"
#: Must match `app.core.database.TENANT_SETTING`, as the P2 migration's copy does.
TENANT_SETTING = "kryova.organisation_ids"

_TENANTS = f"string_to_array(nullif(current_setting('{TENANT_SETTING}', true), ''), ',')"

APPEND_ONLY_FUNCTION = "kryova_audit_append_only"
APPEND_ONLY_MESSAGE = "audit_events is append-only"


def _schema() -> str:
    """The schema this migration is running against.

    `migrations/env.py` sets `search_path` on the direct (non-pooler) endpoint
    before Alembic runs, so `current_schema()` is the application's schema.
    Reading it back beats importing application settings -- the same reasoning
    the P2 migration records.
    """
    return op.get_bind().scalar(text("SELECT current_schema()")) or "public"


def append_only_statements(schema: str) -> list[str]:
    """Make `audit_events` refuse UPDATE, DELETE and TRUNCATE.

    A copy of `app.models.audit.append_only_statements("postgresql", schema)`,
    pinned equal to it by the tests. The single `%` in the RAISE format string
    is correct and is not a typo for `%%`: `%` is plpgsql's placeholder, and
    nothing interpolates this string on the way to the server because it is
    executed with no bind parameters.
    """
    qualified = f'"{schema}"."audit_events"'
    function = f'"{schema}"."{APPEND_ONLY_FUNCTION}"'
    return [
        f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger AS $$\n"
        f"BEGIN\n"
        f"    RAISE EXCEPTION '{APPEND_ONLY_MESSAGE}: % is refused', TG_OP\n"
        f"        USING ERRCODE = 'restrict_violation';\n"
        f"    RETURN NULL;\n"
        f"END;\n"
        f"$$ LANGUAGE plpgsql",
        f"DROP TRIGGER IF EXISTS audit_events_append_only ON {qualified}",
        f"CREATE TRIGGER audit_events_append_only "
        f"BEFORE UPDATE OR DELETE ON {qualified} "
        f"FOR EACH ROW EXECUTE FUNCTION {function}()",
        f"DROP TRIGGER IF EXISTS audit_events_no_truncate ON {qualified}",
        f"CREATE TRIGGER audit_events_no_truncate "
        f"BEFORE TRUNCATE ON {qualified} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {function}()",
        f"REVOKE UPDATE, DELETE, TRUNCATE ON {qualified} FROM PUBLIC",
    ]


def append_only_teardown_statements(schema: str) -> list[str]:
    qualified = f'"{schema}"."audit_events"'
    function = f'"{schema}"."{APPEND_ONLY_FUNCTION}"'
    return [
        f"DROP TRIGGER IF EXISTS audit_events_no_truncate ON {qualified}",
        f"DROP TRIGGER IF EXISTS audit_events_append_only ON {qualified}",
        f"DROP FUNCTION IF EXISTS {function}()",
    ]


def audit_rls_statements(schema: str) -> list[str]:
    """Tenant isolation on the log, with one named exception for staff.

    Exposed as a function for the reason the P2 migration exposes
    `rls_statements`: an isolation test can then apply the policy that ships
    rather than a hand-copied approximation of it.
    """
    qualified = f'"{schema}"."audit_events"'
    predicate = (
        f"({_TENANTS} IS NULL "
        f"OR current_setting('{STAFF_SETTING}', true) = 'on' "
        f"OR organisation_id = ANY({_TENANTS}))"
    )
    return [
        f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS audit_events_tenant_isolation ON {qualified}",
        f"CREATE POLICY audit_events_tenant_isolation ON {qualified} "
        f"USING {predicate} WITH CHECK (true)",
    ]


def audit_rls_teardown_statements(schema: str) -> list[str]:
    qualified = f'"{schema}"."audit_events"'
    return [
        f"DROP POLICY IF EXISTS audit_events_tenant_isolation ON {qualified}",
        f"ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {qualified} DISABLE ROW LEVEL SECURITY",
    ]


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('audit_events',
    sa.Column('sequence', sa.BigInteger(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('action', sa.Enum('staff.granted', 'staff.revoked', 'impersonation.started', 'impersonation.escalated', 'impersonation.ended', 'impersonation.request', 'impersonation.write_refused', 'job.retried', 'job.failed', 'quota.read', 'admin.refused', name='auditaction', native_enum=False, length=48), nullable=False),
    sa.Column('outcome', sa.Enum('succeeded', 'refused', 'failed', 'permitted', name='auditoutcome', native_enum=False, length=16), nullable=False),
    sa.Column('actor_user_id', sa.String(length=36), nullable=True),
    sa.Column('actor_email', sa.String(length=320), nullable=True),
    sa.Column('subject_user_id', sa.String(length=36), nullable=True),
    sa.Column('subject_email', sa.String(length=320), nullable=True),
    sa.Column('impersonated', sa.Boolean(), nullable=False),
    sa.Column('organisation_id', sa.String(length=36), nullable=True),
    sa.Column('target_type', sa.String(length=64), nullable=True),
    sa.Column('target_id', sa.String(length=64), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('user_agent', sa.String(length=400), nullable=True),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('previous_hash', sa.String(length=64), nullable=True),
    sa.Column('entry_hash', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_events_action_occurred', 'audit_events', ['action', 'occurred_at'], unique=False)
    op.create_index('ix_audit_events_actor_occurred', 'audit_events', ['actor_user_id', 'occurred_at'], unique=False)
    op.create_index('ix_audit_events_occurred', 'audit_events', ['occurred_at'], unique=False)
    op.create_index('ix_audit_events_org_occurred', 'audit_events', ['organisation_id', 'occurred_at'], unique=False)
    op.create_index('ix_audit_events_sequence', 'audit_events', ['sequence'], unique=True)
    op.create_index('ix_audit_events_subject_occurred', 'audit_events', ['subject_user_id', 'occurred_at'], unique=False)
    op.create_table('impersonation_sessions',
    sa.Column('actor_user_id', sa.String(length=36), nullable=False),
    sa.Column('subject_user_id', sa.String(length=36), nullable=False),
    sa.Column('mode', sa.Enum('read', 'write', name='impersonationmode', native_enum=False, length=8), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('escalation_reason', sa.String(length=500), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['subject_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_impersonation_actor_ended', 'impersonation_sessions', ['actor_user_id', 'ended_at'], unique=False)
    op.create_index(op.f('ix_impersonation_sessions_actor_user_id'), 'impersonation_sessions', ['actor_user_id'], unique=False)
    op.create_index(op.f('ix_impersonation_sessions_subject_user_id'), 'impersonation_sessions', ['subject_user_id'], unique=False)
    op.create_index('ix_impersonation_subject_ended', 'impersonation_sessions', ['subject_user_id', 'ended_at'], unique=False)
    op.create_table('staff_grants',
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.Enum('support', 'operator', 'platform_admin', name='staffrole', native_enum=False, length=24), nullable=False),
    sa.Column('granted_by_id', sa.String(length=36), nullable=True),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['granted_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_staff_grants_user_id'), 'staff_grants', ['user_id'], unique=False)
    op.create_index('ix_staff_grants_user_revoked', 'staff_grants', ['user_id', 'revoked_at'], unique=False)
    # ### end Alembic commands ###

    schema = _schema()
    for statement in append_only_statements(schema):
        op.execute(statement)
    for statement in audit_rls_statements(schema):
        op.execute(statement)


def downgrade() -> None:
    """Downgrade schema."""
    schema = _schema()
    for statement in audit_rls_teardown_statements(schema):
        op.execute(statement)
    # The triggers go with the table; the function would not, so it is dropped
    # explicitly rather than left behind for the next upgrade to collide with.
    for statement in append_only_teardown_statements(schema):
        op.execute(statement)

    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('ix_staff_grants_user_revoked', table_name='staff_grants')
    op.drop_index(op.f('ix_staff_grants_user_id'), table_name='staff_grants')
    op.drop_table('staff_grants')
    op.drop_index('ix_impersonation_subject_ended', table_name='impersonation_sessions')
    op.drop_index(op.f('ix_impersonation_sessions_subject_user_id'), table_name='impersonation_sessions')
    op.drop_index(op.f('ix_impersonation_sessions_actor_user_id'), table_name='impersonation_sessions')
    op.drop_index('ix_impersonation_actor_ended', table_name='impersonation_sessions')
    op.drop_table('impersonation_sessions')
    op.drop_index('ix_audit_events_subject_occurred', table_name='audit_events')
    op.drop_index('ix_audit_events_sequence', table_name='audit_events')
    op.drop_index('ix_audit_events_org_occurred', table_name='audit_events')
    op.drop_index('ix_audit_events_occurred', table_name='audit_events')
    op.drop_index('ix_audit_events_actor_occurred', table_name='audit_events')
    op.drop_index('ix_audit_events_action_occurred', table_name='audit_events')
    op.drop_table('audit_events')
    # ### end Alembic commands ###
