"""simulation analysis kind and plane thickness

Backwards-compatible by construction, which is the requirement for every
migration here. `analysis` is NOT NULL with a server default of `solid`, so
every simulation already in the table keeps exactly the meaning it had: they
were all solid runs, and they now say so. `thickness_mm` is nullable because a
solid has no out-of-plane thickness to state -- its geometry carries one.


Revision ID: 490d3f517ca6
Revises: c7e2a9d4f1b3
Create Date: 2026-09-09 00:26:36.716597

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '490d3f517ca6'
down_revision: Union[str, Sequence[str], None] = 'c7e2a9d4f1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('simulation_jobs', sa.Column('analysis', sa.String(length=16), server_default='solid', nullable=False))
    op.add_column('simulation_jobs', sa.Column('thickness_mm', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('simulation_jobs', 'thickness_mm')
    op.drop_column('simulation_jobs', 'analysis')
