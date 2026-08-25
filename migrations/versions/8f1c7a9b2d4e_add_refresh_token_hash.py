"""add refresh token hash"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f1c7a9b2d4e"
down_revision: str | None = "286a04c00025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("refresh_token_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "refresh_token_hash")
