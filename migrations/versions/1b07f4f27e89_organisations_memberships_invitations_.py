"""organisations, memberships, invitations and RLS tenancy

Revision ID: 1b07f4f27e89
Revises: 90957dafff41
Create Date: 2026-09-06 07:30:14.395600

Phase P2. Three things happen here and the order between them is the whole
migration:

1. the tenant tables are created;
2. **every existing project is given an owning organisation** -- a personal one
   per user, created here, with that user as its `owner`. `projects.organisation_id`
   is only made NOT NULL after the backfill has run, because a migration that
   leaves rows orphaned is a migration that cannot be deployed, and an orphaned
   project is worse than a broken one: it sits outside every RLS policy, which
   is to say visible to nobody and protected by nothing;
3. row-level security is enabled on the tenant-owned tables.

**`FORCE ROW LEVEL SECURITY` is not optional here.** Postgres exempts a table's
owner from its own policies, and the application connects as the role that owns
these tables. Without FORCE, every policy below is decoration and the isolation
test would pass against a database enforcing nothing.

The policies stand aside when no tenant context is set. That is deliberate and
it is the honest trade: `SET LOCAL` is issued once per request by
`get_current_user`, so *every* query in an authenticated request is covered,
while migrations, background jobs and maintenance scripts -- which have no user
and no tenant -- keep working. What RLS catches is the query that forgot its
`WHERE organisation_id = ...`, which is the thing that actually gets forgotten;
what it cannot catch is somebody deleting the one line that publishes the
context, which is why `tests/test_tenancy_rls.py` asserts the request path sets
it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '1b07f4f27e89'
down_revision: Union[str, Sequence[str], None] = '90957dafff41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The GUC the policies read. Must match `app.core.database.TENANT_SETTING`;
#: `tests/test_tenancy_rls.py` asserts the two are the same string, because a
#: typo here is a policy that never matches and therefore never protects.
TENANT_SETTING = "kryova.organisation_ids"

#: The caller's tenants as a text[], or NULL when no context has been
#: established. `current_setting(..., true)` returns NULL for an unset
#: parameter; `nullif(..., '')` folds an empty one into the same answer, so
#: "not set" has exactly one spelling.
_TENANTS = (
    f"string_to_array(nullif(current_setting('{TENANT_SETTING}', true), ''), ',')"
)

#: Tables owning a tenant column directly, and the column that carries it.
_DIRECT: list[tuple[str, str]] = [
    ("organisations", "id"),
    ("memberships", "organisation_id"),
    ("organisation_invitations", "organisation_id"),
    ("projects", "organisation_id"),
]

#: Tables that reach their tenant through `projects`. The subquery is itself
#: subject to the projects policy, which is harmless -- it is the same
#: predicate -- and means a child row cannot be reached through a project the
#: caller cannot see either.
_VIA_PROJECT: list[str] = ["geometry_versions", "simulation_jobs"]


def _predicate(column: str) -> str:
    return f"({_TENANTS} IS NULL OR {column} = ANY({_TENANTS}))"


def _project_predicate(schema: str) -> str:
    return (
        f"({_TENANTS} IS NULL OR EXISTS ("
        f'SELECT 1 FROM "{schema}".projects p '
        f"WHERE p.id = project_id AND p.organisation_id = ANY({_TENANTS})))"
    )


def rls_statements(schema: str) -> list[str]:
    """The RLS DDL, as executable SQL, for a given schema.

    Exposed as a function rather than inlined so the isolation test can apply
    *this* DDL to its own schema instead of a hand-copied approximation of it.
    A test that pins a second copy of the policy proves the copy works.
    """
    statements: list[str] = []
    for table, column in _DIRECT:
        qualified = f'"{schema}"."{table}"'
        statements += [
            f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {qualified}",
            f"CREATE POLICY {table}_tenant_isolation ON {qualified} "
            f"USING {_predicate(column)} WITH CHECK {_predicate(column)}",
        ]
    for table in _VIA_PROJECT:
        qualified = f'"{schema}"."{table}"'
        predicate = _project_predicate(schema)
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
    for table in [table for table, _ in _DIRECT] + _VIA_PROJECT:
        qualified = f'"{schema}"."{table}"'
        statements += [
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {qualified}",
            f"ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {qualified} DISABLE ROW LEVEL SECURITY",
        ]
    return statements


def _backfill(schema: str) -> str:
    """One personal organisation per user, and every project placed in one.

    A loop rather than a set-based INSERT ... SELECT because the organisation's
    generated id has to be carried into two more statements; `RETURNING` cannot
    hand back the user it was created for. The row count is the number of
    accounts, which at this point in the product's life is small and at any
    point is bounded by something a human signed up for.

    Users who already hold a membership are skipped, so re-running against a
    partially migrated database is safe.
    """
    return f"""
DO $$
DECLARE
    account RECORD;
    org_id text;
    local_part text;
BEGIN
    FOR account IN SELECT id, email, full_name FROM "{schema}".users LOOP
        SELECT o.id INTO org_id
        FROM "{schema}".organisations o
        JOIN "{schema}".memberships m ON m.organisation_id = o.id
        WHERE m.user_id = account.id AND o.is_personal
        LIMIT 1;

        IF org_id IS NULL THEN
            org_id := gen_random_uuid()::text;
            local_part := split_part(coalesce(account.email, ''), '@', 1);
            INSERT INTO "{schema}".organisations
                (id, name, slug, is_personal, created_at, updated_at)
            VALUES (
                org_id,
                coalesce(nullif(account.full_name, ''), nullif(local_part, ''), 'Personal'),
                coalesce(
                    nullif(left(regexp_replace(lower(local_part), '[^a-z0-9]+', '-', 'g'), 32), ''),
                    'user'
                ) || '-' || left(replace(account.id, '-', ''), 8),
                true,
                now(),
                now()
            );
            INSERT INTO "{schema}".memberships
                (id, organisation_id, user_id, role, domain_role, created_at, updated_at)
            VALUES (gen_random_uuid()::text, org_id, account.id, 'owner', 'engineer', now(), now());
        END IF;

        UPDATE "{schema}".projects
        SET organisation_id = org_id
        WHERE owner_id = account.id AND organisation_id IS NULL;
    END LOOP;

    IF EXISTS (SELECT 1 FROM "{schema}".projects WHERE organisation_id IS NULL) THEN
        RAISE EXCEPTION
            'P2 backfill left projects with no organisation; refusing to continue';
    END IF;
END
$$;
"""


def _schema() -> str:
    """The schema this migration is running against.

    `migrations/env.py` sets `search_path` on the direct (non-pooler) endpoint
    before Alembic runs, so `current_schema()` is the application's schema.
    Reading it back beats importing application settings: this file has to keep
    meaning the same thing years after the settings module has moved.
    """
    return op.get_bind().scalar(text("SELECT current_schema()")) or "public"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('organisations',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('is_personal', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_organisations_slug'), 'organisations', ['slug'], unique=True)
    op.create_table('memberships',
    sa.Column('organisation_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.Enum('owner', 'admin', 'member', 'viewer', name='orgrole', native_enum=False, length=16), nullable=False),
    sa.Column('domain_role', sa.Enum('engineer', 'reviewer', 'operator', name='domainrole', native_enum=False, length=16), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organisation_id', 'user_id', name='uq_membership_org_user')
    )
    op.create_index(op.f('ix_memberships_organisation_id'), 'memberships', ['organisation_id'], unique=False)
    op.create_index(op.f('ix_memberships_user_id'), 'memberships', ['user_id'], unique=False)
    op.create_index('ix_memberships_user_org', 'memberships', ['user_id', 'organisation_id'], unique=False)
    op.create_table('organisation_invitations',
    sa.Column('organisation_id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('role', sa.Enum('owner', 'admin', 'member', 'viewer', name='orgrole', native_enum=False, length=16), nullable=False),
    sa.Column('domain_role', sa.Enum('engineer', 'reviewer', 'operator', name='domainrole', native_enum=False, length=16), nullable=True),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('invited_by_id', sa.String(length=36), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('accepted_by_id', sa.String(length=36), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['accepted_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['invited_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_org_invitations_org_email', 'organisation_invitations', ['organisation_id', 'email'], unique=False)
    op.create_index(op.f('ix_organisation_invitations_email'), 'organisation_invitations', ['email'], unique=False)
    op.create_index(op.f('ix_organisation_invitations_organisation_id'), 'organisation_invitations', ['organisation_id'], unique=False)
    op.create_index(op.f('ix_organisation_invitations_token_hash'), 'organisation_invitations', ['token_hash'], unique=True)

    # Nullable first: there is nothing to put in it yet.
    op.add_column('projects', sa.Column('organisation_id', sa.String(length=36), nullable=True))

    schema = _schema()
    op.execute(_backfill(schema))

    # Only now, with every row placed, does the column become mandatory.
    op.alter_column('projects', 'organisation_id', nullable=False)
    op.create_index(op.f('ix_projects_organisation_id'), 'projects', ['organisation_id'], unique=False)
    op.create_foreign_key(None, 'projects', 'organisations', ['organisation_id'], ['id'], ondelete='CASCADE')

    for statement in rls_statements(schema):
        op.execute(statement)


def downgrade() -> None:
    """Downgrade schema."""
    for statement in rls_teardown_statements(_schema()):
        op.execute(statement)

    op.drop_constraint('projects_organisation_id_fkey', 'projects', type_='foreignkey')
    op.drop_index(op.f('ix_projects_organisation_id'), table_name='projects')
    op.drop_column('projects', 'organisation_id')
    op.drop_index(op.f('ix_organisation_invitations_token_hash'), table_name='organisation_invitations')
    op.drop_index(op.f('ix_organisation_invitations_organisation_id'), table_name='organisation_invitations')
    op.drop_index(op.f('ix_organisation_invitations_email'), table_name='organisation_invitations')
    op.drop_index('ix_org_invitations_org_email', table_name='organisation_invitations')
    op.drop_table('organisation_invitations')
    op.drop_index('ix_memberships_user_org', table_name='memberships')
    op.drop_index(op.f('ix_memberships_user_id'), table_name='memberships')
    op.drop_index(op.f('ix_memberships_organisation_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_index(op.f('ix_organisations_slug'), table_name='organisations')
    op.drop_table('organisations')
