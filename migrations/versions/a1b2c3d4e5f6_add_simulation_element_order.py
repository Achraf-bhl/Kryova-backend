"""add simulation element order

Quadratic (tet10) elements are now an option on a simulation request. The order
is recorded on the job because the mesh itself is not kept: without it a stored
result cannot be reproduced, and a tet4 and a tet10 run of the same load case
are not comparable.

Existing rows all predate the option and were solved with linear tets, so the
backfill is 1 -- which is also the server default, keeping the column
NOT NULL without a rewrite window.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "simulation_jobs",
        sa.Column("element_order", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("simulation_jobs", "element_order")
