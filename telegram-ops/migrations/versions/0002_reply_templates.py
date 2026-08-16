"""Add reusable reply templates and normalize rule delivery flags."""

from alembic import op
import sqlalchemy as sa


revision = "0002_reply_templates"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reply_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False, unique=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.execute("UPDATE rules SET group_reply_enabled = send_mode IN ('group_reply', 'both')")
    op.execute("UPDATE rules SET private_message_enabled = send_mode IN ('private_message', 'both')")


def downgrade() -> None:
    op.drop_table("reply_templates")
