"""project management: archive, tags, stars, template

Revision ID: c7a41e9d2b60
Revises: 16c0cc4190d5
Create Date: 2026-10-05 09:30:00.000000

ROAD_TO_10 7.1, 7.6 and 7.7.

`projects` gains `archived_at` (null = live), `tags` (a JSON list, default `[]`) and
`template_key` (the mission rung a project was started from, null for every existing row),
and `project_stars` records one user's star on one project. Existing rows are untouched in
meaning: nothing is archived, tagged or starred until somebody says so.

**Rollback note.** `downgrade` drops the three columns and the table, so every archive flag,
tag, star and recorded template is lost -- and an archived project **reappears in every
list**, because the flag that hid it is gone. Roll the code back with the schema: the code
reads `archived_at` and `tags` on every project and fails on a schema without them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7a41e9d2b60'
down_revision: Union[str, Sequence[str], None] = '16c0cc4190d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_projects_archived_at'), 'projects', ['archived_at'], unique=False)
    op.add_column(
        'projects',
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    )
    op.add_column('projects', sa.Column('template_key', sa.String(length=64), nullable=True))
    op.create_table(
        'project_stars',
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_project_stars_user', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_project_stars_project', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'project_id', name='uq_project_star_user_project'),
    )
    op.create_index(op.f('ix_project_stars_user_id'), 'project_stars', ['user_id'], unique=False)
    op.create_index('ix_project_stars_project', 'project_stars', ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_project_stars_project', table_name='project_stars')
    op.drop_index(op.f('ix_project_stars_user_id'), table_name='project_stars')
    op.drop_table('project_stars')
    op.drop_column('projects', 'template_key')
    op.drop_column('projects', 'tags')
    op.drop_index(op.f('ix_projects_archived_at'), table_name='projects')
    op.drop_column('projects', 'archived_at')
