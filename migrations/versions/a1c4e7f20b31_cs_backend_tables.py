"""cs_backend tables (팀 DB에 칸이 없는 백엔드 전용 정보)

Revision ID: a1c4e7f20b31
Revises:
Create Date: 2026-10-02 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1c4e7f20b31'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "cs_backend"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "conversation_ext",
        sa.Column("conversation_id", sa.BigInteger(), primary_key=True),
        sa.Column("inquiry_no", sa.Text(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("order_no", sa.Text(), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("last_answer_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["conversation_id"], ["cs_chatbot.conversation.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["cs_chatbot.orders.id"]),
        sa.CheckConstraint("close_reason IN ('USER','NEW_CHAT','TIMEOUT')", name="ck_conversation_ext_close_reason"),
        sa.UniqueConstraint("inquiry_no", name="uq_conversation_ext_inquiry_no"),
        schema=SCHEMA,
    )
    op.create_index("ix_conversation_ext_category", "conversation_ext", ["category"], schema=SCHEMA)
    op.create_table(
        "analysis_ext",
        sa.Column("analysis_id", sa.BigInteger(), primary_key=True),
        sa.Column("reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["analysis_id"], ["cs_chatbot.ai_analysis.id"]),
        schema=SCHEMA,
    )
    op.create_table(
        "policy_file",
        sa.Column("policy_document_id", sa.BigInteger(), primary_key=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("stored_name", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["policy_document_id"], ["cs_chatbot.policy_document.id"]),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("policy_file", schema=SCHEMA)
    op.drop_table("analysis_ext", schema=SCHEMA)
    op.drop_index("ix_conversation_ext_category", table_name="conversation_ext", schema=SCHEMA)
    op.drop_table("conversation_ext", schema=SCHEMA)
