"""E10.1 a transient conduction case, and a borrowed temperature field

Revision ID: 5f1bc5c58c49
Revises: b5d0e3dae224

Two nullable JSONB columns on `simulation_jobs`. `transient_case` holds the case a
`thermal-transient` run solves; `temperature_source` names the thermal run whose
field a solid run carries, with the digest of that run's archive. Null is what
every existing row means — not transient, no borrowed field — so nothing is
backfilled and no row changes character.

A sibling of `thermal_case` rather than a reuse of it: a steady `ThermalCase`
validates a transient payload and drops its time axis without a word, so one
column holding both shapes would make a row's own case unreadable without first
reading `analysis`.

**Rollback note.** `downgrade` drops both columns. Every transient run's case is
lost, and every coupled structural run loses the record of which temperatures it
carried; the result summaries and stored fields survive, but the inputs that
produced them do not, so those rows stop being reproducible — and a coupled run
reads afterwards as though it had been solved at room temperature. Export both
columns before rolling back if any of those runs matter.
Create Date: 2026-09-14 18:15:48.816481

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5f1bc5c58c49'
down_revision: Union[str, Sequence[str], None] = 'b5d0e3dae224'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'simulation_jobs',
        sa.Column(
            'transient_case',
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'),
            nullable=True,
        ),
    )
    op.add_column(
        'simulation_jobs',
        sa.Column(
            'temperature_source',
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('simulation_jobs', 'temperature_source')
    op.drop_column('simulation_jobs', 'transient_case')
