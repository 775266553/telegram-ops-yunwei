"""Add recurring group schedule fields to rules."""

from alembic import op
import sqlalchemy as sa


revision = "0003_rule_schedules"
down_revision = "0002_reply_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rules", sa.Column("schedule_account_id", sa.Integer(), nullable=True))
    op.add_column("rules", sa.Column("schedule_chat_id", sa.Integer(), nullable=True))
    op.add_column("rules", sa.Column("schedule_interval_minutes", sa.Integer(), nullable=True))
    op.add_column("rules", sa.Column("schedule_next_run_at", sa.DateTime(), nullable=True))
    op.create_index("ix_rules_schedule_due", "rules", ["match_mode", "enabled", "schedule_next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_rules_schedule_due", table_name="rules")
    op.drop_column("rules", "schedule_next_run_at")
    op.drop_column("rules", "schedule_interval_minutes")
    op.drop_column("rules", "schedule_chat_id")
    op.drop_column("rules", "schedule_account_id")
