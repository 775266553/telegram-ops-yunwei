"""Track whether a synced chat is still available to an account."""

from alembic import op
import sqlalchemy as sa


revision = "0005_chat_availability"
down_revision = "0004_schedule_targets_and_jitter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column("chats", "is_available", server_default=None)


def downgrade() -> None:
    op.drop_column("chats", "is_available")
