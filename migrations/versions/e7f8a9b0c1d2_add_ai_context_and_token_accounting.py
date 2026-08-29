"""add ai context management and token accounting

Three things the agent layer could not do before:

* carry a conversation past the model's context window (`summary`,
  `summary_through_sequence`);
* remember which CATIA document a conversation owns, so resuming it tomorrow
  reopens the same part (`catia_document`, `catia_state`);
* meter LLM spend the way FEA compute is already metered (`ai_token_usage`,
  plus denormalised totals on the conversation).

Every added column is nullable or carries a server default, so the migration is
backwards-compatible with rows written by the previous release.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column(
            "summary_through_sequence",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "conversations",
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "completion_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "conversations", sa.Column("catia_state", postgresql.JSONB(), nullable=True)
    )

    op.add_column(
        "conversation_messages", sa.Column("duration_ms", sa.Integer(), nullable=True)
    )

    op.create_table(
        "ai_token_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL, not CASCADE: deleting a conversation must not erase the
        # record of what it spent.
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "completion_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_token_usage_user_id", "ai_token_usage", ["user_id"])
    op.create_index(
        "ix_ai_token_usage_conversation_id", "ai_token_usage", ["conversation_id"]
    )
    # The per-user daily budget check is exactly this lookup, on every turn.
    op.create_index(
        "ix_ai_token_usage_user_day", "ai_token_usage", ["user_id", "usage_date"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_token_usage_user_day", table_name="ai_token_usage")
    op.drop_index("ix_ai_token_usage_conversation_id", table_name="ai_token_usage")
    op.drop_index("ix_ai_token_usage_user_id", table_name="ai_token_usage")
    op.drop_table("ai_token_usage")

    op.drop_column("conversation_messages", "duration_ms")

    op.drop_column("conversations", "catia_state")
    op.drop_column("conversations", "completion_tokens")
    op.drop_column("conversations", "prompt_tokens")
    op.drop_column("conversations", "summary_through_sequence")
    op.drop_column("conversations", "summary")
