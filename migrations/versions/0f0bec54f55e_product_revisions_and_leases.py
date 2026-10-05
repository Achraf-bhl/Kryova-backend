"""product revisions and leases

Revision ID: 0f0bec54f55e
Revises: c7a41e9d2b60
Create Date: 2026-10-05 08:59:59.529098

ROAD_TO_10 9.4. `product_revisions` is a product's append-only revision chain (the structure
verbatim plus its digest) and `product_leases` is its lease book, one row per leased component.
Both exist so `app/assembly/store.py` can run `ProductRepository`'s rules across processes under
a transaction-scoped advisory lock. Nothing reads them yet -- no route exposes a product -- so
the migration changes no behaviour of anything shipped.

Both tables carry the project's `organisation_id` and join the RLS set with the predicate every
tenant table shares (see `7da3113b27aa`); `tests/test_tenancy_rls.py` collects `rls_statements`
from every migration that defines one.

**Rollback note.** `downgrade` drops both tables: every stored product revision and every lease
goes with them, and neither is reconstructible from anywhere else. Nothing else references them,
so no other table or column changes; code from before this revision does not read them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

#: Must match `app.core.database.TENANT_SETTING`, as every other copy does.
TENANT_SETTING = "kryova.organisation_ids"

_TENANTS = f"string_to_array(nullif(current_setting('{TENANT_SETTING}', true), ''), ',')"

#: Tables added here that own a tenant column directly.
_TENANT_TABLES: list[str] = ["product_revisions", "product_leases"]


def _predicate(column: str = "organisation_id") -> str:
    return f"({_TENANTS} IS NULL OR {column} = ANY({_TENANTS}))"


def rls_statements(schema: str) -> list[str]:
    """The RLS DDL for a given schema, as executable SQL (applied by the isolation test too)."""
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
revision: str = '0f0bec54f55e'
down_revision: Union[str, Sequence[str], None] = 'c7a41e9d2b60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('product_leases',
    sa.Column('organisation_id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('product_key', sa.String(length=128), nullable=False),
    sa.Column('component', sa.String(length=255), nullable=False),
    sa.Column('holder', sa.String(length=255), nullable=False),
    sa.Column('taken_at', sa.Float(), nullable=False),
    sa.Column('expires_at', sa.Float(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'product_key', 'component', name='uq_product_lease')
    )
    op.create_index(op.f('ix_product_leases_organisation_id'), 'product_leases', ['organisation_id'], unique=False)
    op.create_index('ix_product_leases_product', 'product_leases', ['project_id', 'product_key'], unique=False)
    op.create_table('product_revisions',
    sa.Column('organisation_id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('product_key', sa.String(length=128), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('digest', sa.String(length=64), nullable=False),
    sa.Column('parent', sa.String(length=64), nullable=True),
    sa.Column('author', sa.String(length=255), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('at', sa.Float(), nullable=True),
    sa.Column('structure', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('change', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'product_key', 'number', name='uq_product_revision_number')
    )
    op.create_index(op.f('ix_product_revisions_organisation_id'), 'product_revisions', ['organisation_id'], unique=False)
    op.create_index('ix_product_revisions_product', 'product_revisions', ['project_id', 'product_key'], unique=False)

    # SQLite has no row-level security and the offline suite runs on it.
    if op.get_bind().dialect.name == "postgresql":
        for statement in rls_statements(settings.db_schema):
            op.execute(statement)


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        for statement in rls_teardown_statements(settings.db_schema):
            op.execute(statement)
    op.drop_index('ix_product_revisions_product', table_name='product_revisions')
    op.drop_index(op.f('ix_product_revisions_organisation_id'), table_name='product_revisions')
    op.drop_table('product_revisions')
    op.drop_index('ix_product_leases_product', table_name='product_leases')
    op.drop_index(op.f('ix_product_leases_organisation_id'), table_name='product_leases')
    op.drop_table('product_leases')
