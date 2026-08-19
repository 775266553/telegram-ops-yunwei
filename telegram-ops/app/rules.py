import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from jinja2 import Template as JinjaTemplate

from app.config import get_settings
from app.enums import (
    ACCOUNT_BLOCKED_SEND_STATUSES,
    LEAD_BLOCKED,
    LEAD_DUPLICATE,
    LEAD_NEW,
    LEAD_QUEUED,
    QUEUE_PENDING,
    SEND_MODE_BOTH,
    SEND_MODE_GROUP_REPLY,
    SEND_MODE_PRIVATE_MESSAGE,
)
from app.models import Account, Chat, Lead, Rule, RuleAccount, RuleChat, SendQueue, UserGuard


@dataclass
class MessageContext:
    account_id: int
    chat_id: int
    telegram_chat_id: int
    message_id: int | None
    telegram_user_id: int | None
    username: str | None
    text: str


def split_keywords(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[\n,，]+", value or "") if item.strip()]


def rule_matches(rule: Rule, text: str) -> bool:
    if not rule.enabled:
        return False
    if not text:
        return False
    if rule.match_mode == "regex":
        for pattern in split_keywords(rule.keywords):
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    return True
            except re.error:
                continue
        return False
    lower_text = text.lower()
    return any(keyword.lower() in lower_text for keyword in split_keywords(rule.keywords))


def render_template(template: str, ctx: MessageContext, rule: Rule) -> str:
    data = {
        "username": ctx.username or "",
        "user_id": ctx.telegram_user_id or "",
        "chat_id": ctx.telegram_chat_id,
        "message_id": ctx.message_id or "",
        "rule_name": rule.name,
        "message_text": ctx.text,
    }
    # 兼容旧的 $变量名 语法，转换为 Jinja2 语法
    template = re.sub(r'\$(\w+)', r'{{ \1 }}', template)
    # 使用 Jinja2 替代 string.Template，支持更灵活的模板语法
    tpl = JinjaTemplate(template)
    return tpl.render(**data)


def load_candidate_rules(db: Session, ctx: MessageContext) -> Iterable[Rule]:
    account_rule_ids = [row[0] for row in db.query(RuleAccount.rule_id).filter(RuleAccount.account_id == ctx.account_id).all()]
    chat_rule_ids = [row[0] for row in db.query(RuleChat.rule_id).filter(RuleChat.chat_id == ctx.chat_id).all()]
    has_account_scope = select(RuleAccount.rule_id).subquery()
    has_chat_scope = select(RuleChat.rule_id).subquery()

    return (
        db.query(Rule)
        .filter(
            Rule.enabled.is_(True),
            or_(~Rule.id.in_(has_account_scope), Rule.id.in_(account_rule_ids or [-1])),
            or_(~Rule.id.in_(has_chat_scope), Rule.id.in_(chat_rule_ids or [-1])),
        )
        .all()
    )


def _within_cooldown(db: Session, ctx: MessageContext, rule: Rule) -> bool:
    if not ctx.telegram_user_id:
        return False
    since = datetime.utcnow() - timedelta(seconds=rule.cooldown_seconds or get_settings().default_user_cooldown_seconds)
    existing = (
        db.query(SendQueue.id)
        .filter(
            SendQueue.telegram_user_id == ctx.telegram_user_id,
            SendQueue.rule_id == rule.id,
            SendQueue.created_at >= since,
            SendQueue.status.in_(["pending", "sending", "sent", "paused", "flood_wait"]),
        )
        .first()
    )
    return existing is not None


def _user_daily_limit_hit(db: Session, ctx: MessageContext) -> bool:
    if not ctx.telegram_user_id:
        return False
    settings = get_settings()
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    count = (
        db.query(func.count(SendQueue.id))
        .filter(
            SendQueue.telegram_user_id == ctx.telegram_user_id,
            SendQueue.created_at >= today,
            SendQueue.status.in_(["pending", "sending", "sent", "paused", "flood_wait"]),
        )
        .scalar()
    )
    return count >= settings.default_user_daily_limit


def _account_daily_limit_hit(db: Session, account_id: int, rule: Rule) -> bool:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    account_limit = get_settings().default_account_daily_limit
    account_count = (
        db.query(func.count(SendQueue.id))
        .filter(
            SendQueue.account_id == account_id,
            SendQueue.created_at >= today,
            SendQueue.status.in_(["pending", "sending", "sent", "paused", "flood_wait"]),
        )
        .scalar()
    )
    rule_limit = rule.daily_limit or account_limit
    rule_count = (
        db.query(func.count(SendQueue.id))
        .filter(
            SendQueue.account_id == account_id,
            SendQueue.rule_id == rule.id,
            SendQueue.created_at >= today,
            SendQueue.status.in_(["pending", "sending", "sent", "paused", "flood_wait"]),
        )
        .scalar()
    )
    return account_count >= account_limit or rule_count >= rule_limit


def _user_blocked(db: Session, ctx: MessageContext) -> bool:
    if not ctx.telegram_user_id:
        return False
    guard = db.query(UserGuard).filter(UserGuard.telegram_user_id == ctx.telegram_user_id).first()
    return bool(guard and (guard.blacklisted or guard.unsubscribed))


def initial_scheduled_at(now: datetime | None = None) -> datetime:
    settings = get_settings()
    minimum = max(0, settings.outbound_delay_min_seconds)
    maximum = max(minimum, settings.outbound_delay_max_seconds)
    return (now or datetime.utcnow()) + timedelta(seconds=random.randint(minimum, maximum))


def create_lead_and_queue(db: Session, ctx: MessageContext, rule: Rule) -> Lead:
    lead = Lead(
        source_account_id=ctx.account_id,
        source_chat_id=ctx.chat_id,
        telegram_user_id=ctx.telegram_user_id,
        username=ctx.username,
        message_id=ctx.message_id,
        message_text=ctx.text,
        matched_rule_id=rule.id,
        status=LEAD_NEW,
    )
    db.add(lead)
    db.flush()

    account = db.get(Account, ctx.account_id)
    if not account or account.status in ACCOUNT_BLOCKED_SEND_STATUSES or not account.send_enabled:
        lead.status = LEAD_BLOCKED
        return lead

    if _user_blocked(db, ctx) or _within_cooldown(db, ctx, rule) or _user_daily_limit_hit(db, ctx) or _account_daily_limit_hit(db, ctx.account_id, rule):
        lead.status = LEAD_DUPLICATE
        return lead

    text = render_template(rule.reply_template, ctx, rule)
    mode = rule.send_mode
    scheduled_at = initial_scheduled_at()
    queued_count = 0

    if mode in (SEND_MODE_GROUP_REPLY, SEND_MODE_BOTH) and rule.group_reply_enabled:
        db.add(
            SendQueue(
                account_id=ctx.account_id,
                rule_id=rule.id,
                lead_id=lead.id,
                telegram_user_id=ctx.telegram_user_id,
                destination_chat_id=ctx.telegram_chat_id,
                reply_to_message_id=ctx.message_id,
                message_text=text,
                send_type=SEND_MODE_GROUP_REPLY,
                status=QUEUE_PENDING,
                scheduled_at=scheduled_at,
            )
        )
        db.flush()
        queued_count += 1
        lead.status = LEAD_QUEUED

    private_message_allowed = (
        mode in (SEND_MODE_PRIVATE_MESSAGE, SEND_MODE_BOTH)
        and rule.private_message_enabled
        and account.private_message_enabled
        and ctx.telegram_user_id
        and not _user_daily_limit_hit(db, ctx)
        and not _account_daily_limit_hit(db, ctx.account_id, rule)
    )
    if private_message_allowed:
        if queued_count:
            scheduled_at += timedelta(seconds=max(0, get_settings().outbound_min_interval_seconds))
        db.add(
            SendQueue(
                account_id=ctx.account_id,
                rule_id=rule.id,
                lead_id=lead.id,
                telegram_user_id=ctx.telegram_user_id,
                destination_chat_id=ctx.telegram_user_id,
                message_text=text,
                send_type=SEND_MODE_PRIVATE_MESSAGE,
                status=QUEUE_PENDING,
                scheduled_at=scheduled_at,
            )
        )
        lead.status = LEAD_QUEUED

    return lead


def process_incoming_message(db: Session, ctx: MessageContext) -> list[Lead]:
    chat = db.get(Chat, ctx.chat_id)
    if not chat or not chat.is_available or not chat.enabled or not chat.is_primary_listener:
        return []

    created: list[Lead] = []
    for rule in load_candidate_rules(db, ctx):
        if rule_matches(rule, ctx.text):
            created.append(create_lead_and_queue(db, ctx, rule))
    return created
