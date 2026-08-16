from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base
from app.enums import ACCOUNT_STATUS_ACTIVE, ACCOUNT_STATUS_FLOOD_WAIT, QUEUE_FLOOD_WAIT, QUEUE_PENDING, SEND_MODE_SCHEDULED_GROUP
from app.models import Account, Chat, Rule, RuleScheduleTarget, SendQueue
from app.workers import enqueue_due_schedules, recover_expired_flood_waits, retry_delay_seconds


def test_retry_delay_uses_capped_exponential_backoff(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "outbound_retry_base_seconds", 60)
    monkeypatch.setattr(settings, "outbound_retry_max_seconds", 180)
    monkeypatch.setattr("app.workers.random.randint", lambda minimum, maximum: 0)

    assert retry_delay_seconds(1) == 60
    assert retry_delay_seconds(2) == 120
    assert retry_delay_seconds(3) == 180
    assert retry_delay_seconds(8) == 180


def test_flood_wait_recovery_only_releases_expired_account_jobs():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 1, 1, 12, 0, 0)

    with Session(engine) as db:
        expired = Account(
            name="expired",
            phone="10001",
            api_id=1,
            api_hash_encrypted="hash",
            status=ACCOUNT_STATUS_FLOOD_WAIT,
            flood_wait_until=now - timedelta(seconds=1),
        )
        waiting = Account(
            name="waiting",
            phone="10002",
            api_id=2,
            api_hash_encrypted="hash",
            status=ACCOUNT_STATUS_FLOOD_WAIT,
            flood_wait_until=now + timedelta(minutes=10),
        )
        db.add_all([expired, waiting])
        db.flush()
        expired_job = SendQueue(
            account_id=expired.id,
            rule_id=1,
            message_text="one",
            send_type="group_reply",
            status=QUEUE_FLOOD_WAIT,
        )
        waiting_job = SendQueue(
            account_id=waiting.id,
            rule_id=1,
            message_text="two",
            send_type="group_reply",
            status=QUEUE_FLOOD_WAIT,
        )
        db.add_all([expired_job, waiting_job])
        db.flush()

        assert recover_expired_flood_waits(db, now) == [expired.id]
        db.flush()
        db.refresh(expired)
        db.refresh(waiting)
        db.refresh(expired_job)
        db.refresh(waiting_job)

        assert expired.status == ACCOUNT_STATUS_ACTIVE
        assert expired_job.status == QUEUE_PENDING
        assert waiting.status == ACCOUNT_STATUS_FLOOD_WAIT
        assert waiting_job.status == QUEUE_FLOOD_WAIT


def test_due_schedule_creates_one_queue_and_advances_next_run():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 1, 1, 12, 0, 0)

    with Session(engine) as db:
        account = Account(
            name="scheduled",
            phone="10003",
            api_id=3,
            api_hash_encrypted="hash",
            status=ACCOUNT_STATUS_ACTIVE,
        )
        db.add(account)
        db.flush()
        chat = Chat(
            account_id=account.id,
            telegram_chat_id=-10003,
            title="目标群",
            type="supergroup",
            enabled=True,
        )
        db.add(chat)
        db.flush()
        rule = Rule(
            name="每两小时公告",
            keywords="",
            match_mode="schedule",
            reply_template="公告：$rule_name",
            send_mode=SEND_MODE_SCHEDULED_GROUP,
            schedule_account_id=account.id,
            schedule_chat_id=chat.id,
            schedule_interval_minutes=120,
            schedule_next_run_at=now - timedelta(minutes=1),
            enabled=True,
        )
        db.add(rule)
        db.flush()

        assert enqueue_due_schedules(db, now) == 1
        db.flush()
        job = db.query(SendQueue).one()
        assert job.send_type == SEND_MODE_SCHEDULED_GROUP
        assert job.destination_chat_id == -10003
        assert job.message_text == "公告：每两小时公告"
        assert rule.schedule_next_run_at > now
        assert enqueue_due_schedules(db, now) == 0


def test_due_schedule_creates_one_queue_per_explicit_target_without_cross_product(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 1, 1, 12, 0, 0)
    monkeypatch.setattr("app.workers.random.randint", lambda minimum, maximum: maximum)

    with Session(engine) as db:
        account_a = Account(
            name="账号 A",
            phone="10004",
            api_id=4,
            api_hash_encrypted="hash",
            status=ACCOUNT_STATUS_ACTIVE,
        )
        account_b = Account(
            name="账号 B",
            phone="10005",
            api_id=5,
            api_hash_encrypted="hash",
            status=ACCOUNT_STATUS_ACTIVE,
        )
        db.add_all([account_a, account_b])
        db.flush()
        chat_a1 = Chat(account_id=account_a.id, telegram_chat_id=-100041, title="A 群 1", type="supergroup")
        chat_a2 = Chat(account_id=account_a.id, telegram_chat_id=-100042, title="A 群 2", type="supergroup")
        chat_b1 = Chat(account_id=account_b.id, telegram_chat_id=-100051, title="B 群 1", type="supergroup")
        db.add_all([chat_a1, chat_a2, chat_b1])
        db.flush()
        rule = Rule(
            name="多目标公告",
            keywords="",
            match_mode="schedule",
            reply_template="公告：$rule_name",
            send_mode=SEND_MODE_SCHEDULED_GROUP,
            schedule_interval_min_minutes=120,
            schedule_interval_max_minutes=135,
            schedule_next_run_at=now - timedelta(minutes=1),
            enabled=True,
        )
        db.add(rule)
        db.flush()
        db.add_all([
            RuleScheduleTarget(rule_id=rule.id, account_id=account_a.id, chat_id=chat_a1.id),
            RuleScheduleTarget(rule_id=rule.id, account_id=account_a.id, chat_id=chat_a2.id),
            RuleScheduleTarget(rule_id=rule.id, account_id=account_b.id, chat_id=chat_b1.id),
        ])
        db.flush()

        assert enqueue_due_schedules(db, now) == 3
        jobs = db.query(SendQueue).order_by(SendQueue.destination_chat_id).all()
        assert sorted((job.account_id, job.destination_chat_id) for job in jobs) == sorted([
            (account_a.id, -100041),
            (account_a.id, -100042),
            (account_b.id, -100051),
        ])
        assert rule.schedule_next_run_at == now + timedelta(minutes=135)


def test_due_schedule_uses_fixed_minimum_when_random_range_is_equal(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 1, 1, 12, 0, 0)
    monkeypatch.setattr("app.workers.random.randint", lambda minimum, maximum: maximum)

    with Session(engine) as db:
        account = Account(
            name="fixed",
            phone="10006",
            api_id=6,
            api_hash_encrypted="hash",
            status=ACCOUNT_STATUS_ACTIVE,
        )
        db.add(account)
        db.flush()
        chat = Chat(account_id=account.id, telegram_chat_id=-10006, title="固定周期群", type="supergroup")
        db.add(chat)
        db.flush()
        rule = Rule(
            name="固定周期",
            keywords="",
            match_mode="schedule",
            reply_template="固定内容",
            send_mode=SEND_MODE_SCHEDULED_GROUP,
            schedule_account_id=account.id,
            schedule_chat_id=chat.id,
            schedule_interval_min_minutes=120,
            schedule_interval_max_minutes=120,
            schedule_next_run_at=now,
            enabled=True,
        )
        db.add(rule)
        db.flush()

        assert enqueue_due_schedules(db, now) == 1
        assert rule.schedule_next_run_at == now + timedelta(minutes=120)
