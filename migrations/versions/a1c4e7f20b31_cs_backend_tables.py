"""cs_backend tables (팀 DB 값으로 계산할 수 없는 백엔드 전용 정보: 채팅 종료 사유, 시연 표시)

Revision ID: a1c4e7f20b31
Revises:
Create Date: 2026-10-02 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

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
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["conversation_id"], ["cs_chatbot.conversation.id"]),
        sa.CheckConstraint("close_reason IN ('USER','NEW_CHAT','TIMEOUT')", name="ck_conversation_ext_close_reason"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("conversation_ext", schema=SCHEMA)
