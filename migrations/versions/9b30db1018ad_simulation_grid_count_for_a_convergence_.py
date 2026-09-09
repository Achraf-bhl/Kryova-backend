"""simulation grid count for a convergence study

Backwards-compatible by construction. `grids` is NOT NULL with a server default
of 1, so every simulation already in the table keeps exactly what it did: one
mesh, and now it says so. A convergence study is opt-in and always was -- this
column is what makes it possible to ask for one at all.


Revision ID: 9b30db1018ad
Revises: 490d3f517ca6
Create Date: 2026-09-09 03:07:33.597308

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b30db1018ad'
down_revision: Union[str, Sequence[str], None] = '490d3f517ca6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('simulation_jobs', sa.Column('grids', sa.Integer(), server_default='1', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('simulation_jobs', 'grids')
