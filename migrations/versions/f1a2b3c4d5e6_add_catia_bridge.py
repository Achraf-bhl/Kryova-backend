"""add catia bridge

Four tables for the CATIA desktop bridge: paired workstations, the
conversation-to-document binding, pre-mutation checkpoints, and the append-only
operation log.

Every foreign key here is explicit about deletion, and the choices are not
uniform on purpose:

* `catia_documents.device_id` and everything in `catia_operations` are SET NULL.
  The audit trail has to outlive the hardware and the chat -- a log row whose
  subject vanished still answers "what happened", where a cascaded-away row
  answers nothing.
* `catia_checkpoints.media_id` is RESTRICT, matching `geometry_versions`:
  deleting a blob a checkpoint still points at must fail loudly rather than
  leave a checkpoint that cannot restore.
* Owner and document links cascade, because a deleted user or document has no
  meaningful residue.

See docs/CATIA_BRIDGE_PROTOCOL.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catia_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=True),
        # Only ever the SHA-256 of the device token. 64 hex characters.
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pairing_code", sa.String(16), nullable=True),
        sa.Column("pairing_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("catia_version", sa.String(64), nullable=True),
        sa.Column("bridge_version", sa.String(32), nullable=True),
        sa.Column("is_mock", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_catia_devices_owner_id", "catia_devices", ["owner_id"])
    # Unique, and the lookup on every reconnect.
    op.create_index(
        "ix_catia_devices_token_hash", "catia_devices", ["token_hash"], unique=True
    )
    # Redeeming a code is an unauthenticated lookup by code alone; it must not
    # be a table scan.
    op.create_index(
        "ix_catia_devices_pairing_code", "catia_devices", ["pairing_code"], unique=True
    )
    op.create_index(
        "ix_catia_devices_owner_status", "catia_devices", ["owner_id", "status"]
    )

    op.create_table(
        "catia_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.String(36),
            sa.ForeignKey("catia_devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("doc_name", sa.String(255), nullable=False),
        sa.Column("remote_path", sa.Text(), nullable=True),
        sa.Column("latest_checkpoint_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # One document per conversation, enforced rather than assumed.
        sa.UniqueConstraint("conversation_id", name="uq_catia_document_conversation"),
    )
    op.create_index(
        "ix_catia_documents_conversation_id", "catia_documents", ["conversation_id"]
    )
    op.create_index("ix_catia_documents_device_id", "catia_documents", ["device_id"])

    op.create_table(
        "catia_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("catia_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "media_id",
            sa.String(36),
            sa.ForeignKey("media.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("digest", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("remote_ref", sa.Text(), nullable=True),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_catia_checkpoints_document_id", "catia_checkpoints", ["document_id"])
    op.create_index("ix_catia_checkpoints_media_id", "catia_checkpoints", ["media_id"])
    # Listing a document's checkpoints newest-first is the only read path.
    op.create_index(
        "ix_catia_checkpoints_document_created",
        "catia_checkpoints",
        ["document_id", "created_at"],
    )

    op.create_table(
        "catia_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "device_id",
            sa.String(36),
            sa.ForeignKey("catia_devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_catia_operations_user_id", "catia_operations", ["user_id"])
    op.create_index("ix_catia_operations_tool", "catia_operations", ["tool"])
    # The two questions the log is actually asked: "what happened in this chat"
    # and "what has this workstation been told to do".
    op.create_index(
        "ix_catia_operations_conversation_created",
        "catia_operations",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_catia_operations_device_created",
        "catia_operations",
        ["device_id", "created_at"],
    )


def downgrade() -> None:
    # Children first: checkpoints and operations both reference devices, and
    # documents reference devices too.
    op.drop_table("catia_operations")
    op.drop_table("catia_checkpoints")
    op.drop_table("catia_documents")
    op.drop_table("catia_devices")
