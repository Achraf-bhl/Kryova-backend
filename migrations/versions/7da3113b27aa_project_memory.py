"""project memory

Revision ID: 7da3113b27aa
Revises: d2ec5f1d8253
Create Date: 2026-10-04 23:57:12.809309

ROAD_TO_10 2.7.

* `project_memories` -- facts that outlive one conversation, owned by the project and
  readable by the model only once a person has confirmed them. `state` is `proposed` or
  `confirmed`; `author_id` is null for the agent, which means the agent and not "unknown".

**`project_memories` joins the RLS set directly**, with the predicate every tenant table
shares -- `(tenants IS NULL OR organisation_id = ANY(tenants))` -- because it owns an
`organisation_id` of its own (always its project's, set from the project and never from a
request). The permissive-when-unscoped half does nothing for this table, since nothing reads
it without a signed-in principal, and is kept only because one shared predicate is what makes
the set reviewable. `tests/test_tenancy_rls.py` collects `rls_statements` from every
migration that defines one.

**The user and conversation links are SET NULL, not CASCADE.** Deleting the person who
confirmed a fact, or the conversation in which the agent noticed it, must not delete the
fact: it loses a name and keeps its meaning, which is the honest direction. Deleting the
*project* removes its facts, because they are about it.

**Rollback note.** `downgrade` drops `project_memories` entirely: every fact the users
confirmed and every proposal waiting on them goes with it, and it is reconstructible from
nowhere else -- the transcripts that produced a proposal may be long gone. Nothing else reads
the table, so the agent simply stops being told anything it was told by memory; no other
table or column changes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.core.config import settings

#: Must match `app.core.database.TENANT_SETTING`, as every other copy does.
TENANT_SETTING = "kryova.organisation_ids"

_TENANTS = f"string_to_array(nullif(current_setting('{TENANT_SETTING}', true), ''), ',')"

#: Tables added here that own a tenant column directly.
_TENANT_TABLES: list[str] = ["project_memories"]


def _predicate(column: str = "organisation_id") -> str:
    return f"({_TENANTS} IS NULL OR {column} = ANY({_TENANTS}))"


def rls_statements(schema: str) -> list[str]:
    """The RLS DDL for a given schema, as executable SQL.

    A function rather than inlined so the isolation test can apply *this* DDL to its own
    schema instead of a hand-copied approximation of it.
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


# revision identifiers, used by Alembic.
revision: str = '7da3113b27aa'
down_revision: Union[str, Sequence[str], None] = 'd2ec5f1d8253'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'project_memories',
        sa.Column('organisation_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        # `EnumText` is the model's type; the DDL it emits is this plain VARCHAR.
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('author', sa.String(length=16), nullable=False),
        sa.Column('author_id', sa.String(length=36), nullable=True),
        sa.Column('conversation_id', sa.String(length=36), nullable=True),
        sa.Column('confirmed_by_id', sa.String(length=36), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['confirmed_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_project_memories_organisation_id'),
        'project_memories',
        ['organisation_id'],
        unique=False,
    )
    op.create_index(
        'ix_project_memories_project_state',
        'project_memories',
        ['project_id', 'state', 'created_at'],
        unique=False,
    )

    # SQLite has no row-level security and the offline suite runs on it; the guard is a
    # PostgreSQL one and the database job is where it is enforced.
    if op.get_bind().dialect.name == "postgresql":
        for statement in rls_statements(settings.db_schema):
            op.execute(statement)


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        for statement in rls_teardown_statements(settings.db_schema):
            op.execute(statement)
    op.drop_index('ix_project_memories_project_state', table_name='project_memories')
    op.drop_index(op.f('ix_project_memories_organisation_id'), table_name='project_memories')
    op.drop_table('project_memories')
