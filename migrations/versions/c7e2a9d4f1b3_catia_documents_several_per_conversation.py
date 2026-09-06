"""catia documents: several per conversation, exactly one active

Revision ID: c7e2a9d4f1b3
Revises: b3d7c1f4a920
Create Date: 2026-09-06

Phase 14, the seat-side half. A conversation owned exactly one CATIA document
-- `uq_catia_document_conversation` said so -- and that is what let the agent
say "the part we were working on" without naming a file. An assembly is
inherently several documents, so on a seat there was no route to a second part
at all: measured on ladder prompt S2, the agent called `catia_new_part` seven
times at the refusal.

The mechanic survives as `is_active`: exactly one document per conversation is
the one every scoped call is sent to, enforced by a *partial* unique index so
two rows can never both claim it. Starting a second part deactivates the first
rather than replacing it, so every row keeps its path and its checkpoints
(keyed on `document_id`) and `catia_open_document` can bring any of them back.

Existing rows are the only document of their conversation, so `is_active`
defaults to true for all of them and every conversation comes out of the
upgrade exactly as it went in. `doc_type` is `part` for the same reason -- a
product could not have been bound before this revision.

Rollback note: `downgrade()` restores the single-document constraint, which the
deactivated rows would violate, so it **deletes every inactive document row
first**. Their checkpoints go with them (CASCADE). That is the second part of
every assembly built after this revision; take a dump of `catia_documents` and
`catia_checkpoints` before running it anywhere an assembly has been made.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7e2a9d4f1b3"
down_revision = "b3d7c1f4a920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_catia_document_conversation", "catia_documents", type_="unique")
    op.add_column(
        "catia_documents",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "catia_documents",
        sa.Column("doc_type", sa.String(length=16), nullable=False, server_default="part"),
    )
    op.create_index(
        "uq_catia_document_active",
        "catia_documents",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("uq_catia_document_active", table_name="catia_documents")
    # See the module docstring: the single-document constraint cannot hold
    # while a conversation has more than one row.
    op.execute("DELETE FROM catia_documents WHERE NOT is_active")
    op.drop_column("catia_documents", "doc_type")
    op.drop_column("catia_documents", "is_active")
    op.create_unique_constraint(
        "uq_catia_document_conversation", "catia_documents", ["conversation_id"]
    )
