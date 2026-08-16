"""Support multiple schedule targets and random interval ranges."""

from alembic import op
import sqlalchemy as sa


revision = "0004_schedule_targets_and_jitter"
down_revision = "0003_rule_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rules", sa.Column("schedule_interval_min_minutes", sa.Integer(), nullable=True))
    op.add_column("rules", sa.Column("schedule_interval_max_minutes", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE rules SET schedule_interval_min_minutes = COALESCE(schedule_interval_minutes, 120), "
        "schedule_interval_max_minutes = COALESCE(schedule_interval_minutes, 120)"
    )
    op.create_table(
        "rule_schedule_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.Integer(), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("rule_id", "account_id", "chat_id", name="uq_rule_schedule_target"),
    )
    op.create_index("ix_rule_schedule_targets_rule", "rule_schedule_targets", ["rule_id"])


def downgrade() -> None:
    op.drop_index("ix_rule_schedule_targets_rule", table_name="rule_schedule_targets")
    op.drop_table("rule_schedule_targets")
    op.drop_column("rules", "schedule_interval_max_minutes")
    op.drop_column("rules", "schedule_interval_min_minutes")
