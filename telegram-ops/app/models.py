from datetime import datetime
from typing import List, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import (
    ACCOUNT_STATUS_LOGIN_REQUIRED,
    LEAD_NEW,
    QUEUE_PENDING,
    SEND_MODE_RECORD_ONLY,
)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    api_id: Mapped[int] = mapped_column(Integer, nullable=False)
    api_hash_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    session_string_encrypted: Mapped[str] = mapped_column(Text, nullable=True)
    login_temp_session_string_encrypted: Mapped[str] = mapped_column(Text, nullable=True)
    phone_code_hash: Mapped[str] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(40), default=ACCOUNT_STATUS_LOGIN_REQUIRED, index=True)
    risk_status: Mapped[str] = mapped_column(String(120), nullable=True)
    send_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    private_message_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    proxy_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    proxy_type: Mapped[str] = mapped_column(String(20), nullable=True)
    proxy_host: Mapped[str] = mapped_column(String(255), nullable=True)
    proxy_port: Mapped[int] = mapped_column(Integer, nullable=True)
    proxy_username: Mapped[str] = mapped_column(String(255), nullable=True)
    proxy_password_encrypted: Mapped[str] = mapped_column(Text, nullable=True)

    flood_wait_until: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_health_check_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)

    chats: Mapped[List["Chat"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Chat(Base, TimestampMixin):
    __tablename__ = "chats"
    __table_args__ = (
        UniqueConstraint("account_id", "telegram_chat_id", name="uq_chats_account_tg_chat"),
        Index("ix_chats_tg_enabled", "telegram_chat_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    access_hash: Mapped[str] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_primary_listener: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    account: Mapped[Account] = relationship(back_populates="chats")


class Rule(Base, TimestampMixin):
    __tablename__ = "rules"
    __table_args__ = (
        Index("ix_rules_schedule_due", "match_mode", "enabled", "schedule_next_run_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    keywords: Mapped[str] = mapped_column(Text, nullable=False)
    match_mode: Mapped[str] = mapped_column(String(20), default="keyword")  # keyword / regex / schedule
    reply_template: Mapped[str] = mapped_column(Text, nullable=False)
    send_mode: Mapped[str] = mapped_column(String(40), default=SEND_MODE_RECORD_ONLY, index=True)
    group_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    private_message_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    daily_limit: Mapped[int] = mapped_column(Integer, default=20)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    schedule_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    schedule_chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), nullable=True, index=True)
    schedule_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=True)
    schedule_interval_min_minutes: Mapped[int] = mapped_column(Integer, nullable=True)
    schedule_interval_max_minutes: Mapped[int] = mapped_column(Integer, nullable=True)
    schedule_next_run_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, index=True)


class RuleScheduleTarget(Base):
    __tablename__ = "rule_schedule_targets"
    __table_args__ = (
        UniqueConstraint("rule_id", "account_id", "chat_id", name="uq_rule_schedule_target"),
        Index("ix_rule_schedule_targets_rule", "rule_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)


class ReplyTemplate(Base, TimestampMixin):
    __tablename__ = "reply_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class RuleChat(Base):
    __tablename__ = "rule_chats"
    __table_args__ = (UniqueConstraint("rule_id", "chat_id", name="uq_rule_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True)


class RuleAccount(Base):
    __tablename__ = "rule_accounts"
    __table_args__ = (UniqueConstraint("rule_id", "account_id", name="uq_rule_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)


class Lead(Base, TimestampMixin):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_user_rule_status", "telegram_user_id", "matched_rule_id", "status"),
        Index("ix_leads_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    source_chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), nullable=False, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(255), nullable=True)
    message_id: Mapped[int] = mapped_column(Integer, nullable=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    matched_rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default=LEAD_NEW, index=True)


class SendQueue(Base, TimestampMixin):
    __tablename__ = "send_queue"
    __table_args__ = (
        Index("ix_send_queue_status_due", "status", "scheduled_at"),
        Index("ix_send_queue_user_rule", "telegram_user_id", "rule_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id"), nullable=False, index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), nullable=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=True, index=True)
    destination_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    reply_to_message_id: Mapped[int] = mapped_column(Integer, nullable=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    send_type: Mapped[str] = mapped_column(String(40), nullable=False)  # group_reply / private_message
    status: Mapped[str] = mapped_column(String(40), default=QUEUE_PENDING, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, nullable=True)


class SendLog(Base, TimestampMixin):
    __tablename__ = "send_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    queue_id: Mapped[int] = mapped_column(ForeignKey("send_queue.id"), nullable=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id"), nullable=True, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=True, index=True)
    message_id: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=True)


class UserGuard(Base, TimestampMixin):
    __tablename__ = "user_guards"
    __table_args__ = (UniqueConstraint("telegram_user_id", name="uq_user_guard_tg_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(255), nullable=True)
    blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    unsubscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, nullable=True)
