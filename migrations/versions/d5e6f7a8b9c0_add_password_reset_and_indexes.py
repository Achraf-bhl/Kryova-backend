"""add password reset columns and composite indexes

Password reset needs a hashed token and an expiry on the user row.
The composite indexes speed up the "latest N for project" query pattern used
by both geometry versions and simulation jobs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4a1b2d3e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_reset_token_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("password_reset_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_simulation_project_created",
        "simulation_jobs",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_geometry_project_created",
        "geometry_versions",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_geometry_project_created", table_name="geometry_versions")
    op.drop_index("ix_simulation_project_created", table_name="simulation_jobs")
    op.drop_column("users", "password_reset_expires_at")
    op.drop_column("users", "password_reset_token_hash")
