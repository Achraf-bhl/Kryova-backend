"""E10.2 a laminar flow case

Revision ID: 9947d5ff2340
Revises: 5f1bc5c58c49

One nullable JSONB column on `simulation_jobs`. `flow_case` holds the case a
`flow-laminar` run solves: a fluid, an inlet and an outlet by selector, a cell
size, and optionally the heat the flow carries. Null is what every existing row
means — not a flow run — so nothing is backfilled and no row changes character.

A fourth sibling of `load_case`, `thermal_case` and `transient_case` rather than a
reuse of one: none of them has a fluid, and a flow run has no fixture, no
material and no conductivity field, so sharing a column would make a row's case
unreadable without first reading `analysis`.

**Rollback note.** `downgrade` drops the column. Every flow run's case is lost:
the result summaries and stored fields survive, but the fluid, the inlet, the
outlet, the cell size and any wall heat that produced them do not, so those rows
stop being reproducible. Export the column before rolling back if any flow run
matters.
Create Date: 2026-09-14 20:07:19.210503

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9947d5ff2340'
down_revision: Union[str, Sequence[str], None] = '5f1bc5c58c49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'simulation_jobs',
        sa.Column(
            'flow_case',
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('simulation_jobs', 'flow_case')
