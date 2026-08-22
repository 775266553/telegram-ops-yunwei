import asyncio
import contextlib
from datetime import datetime, timedelta
from typing import Any
import logging
import random

from sqlalchemy import func
from telethon import events
from telethon.errors import (
    AuthKeyUnregisteredError,
    FloodWaitError,
    SessionPasswordNeededError,
    UserDeactivatedBanError,
)
from telethon.sessions import StringSession

from app.crypto import decrypt_text, encrypt_text
from app.database import session_scope
from app.enums import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_BANNED,
    ACCOUNT_STATUS_FLOOD_WAIT,
    ACCOUNT_STATUS_LIMITED,
    ACCOUNT_STATUS_LOGIN_REQUIRED,
    ACCOUNT_STATUS_PROXY_ERROR,
    QUEUE_FAILED,
    QUEUE_FLOOD_WAIT,
    QUEUE_PENDING,
    QUEUE_PAUSED,
    QUEUE_SENDING,
    QUEUE_SENT,
    SEND_MODE_SCHEDULED_GROUP,
)
from app.models import Account, Chat, Rule, RuleScheduleTarget, SendLog, SendQueue
from app.rules import MessageContext, process_incoming_message, render_template
from app.telegram_client import make_client

logger = logging.getLogger(__name__)


def retry_delay_seconds(attempts: int) -> int:
    from app.config import get_settings

    settings = get_settings()
    base = max(1, settings.outbound_retry_base_seconds)
    maximum = max(base, settings.outbound_retry_max_seconds)
    exponential = base * (2 ** max(0, attempts - 1))
    jitter = random.randint(0, min(base, 30))
    return min(maximum, exponential + jitter)


def recover_expired_flood_waits(db, now: datetime) -> list[int]:
    accounts = (
        db.query(Account)
        .filter(
            Account.status == ACCOUNT_STATUS_FLOOD_WAIT,
            Account.flood_wait_until.is_not(None),
            Account.flood_wait_until <= now,
        )
        .all()
    )
    recovered_ids = [account.id for account in accounts]
    for account in accounts:
        account.status = ACCOUNT_STATUS_ACTIVE
        account.flood_wait_until = None
        account.last_error = None
    if recovered_ids:
        (
            db.query(SendQueue)
            .filter(
                SendQueue.account_id.in_(recovered_ids),
                SendQueue.status == QUEUE_FLOOD_WAIT,
            )
            .update({"status": QUEUE_PENDING, "error": None}, synchronize_session=False)
        )
    return recovered_ids


def next_schedule_time(
    current: datetime,
    now: datetime,
    minimum_minutes: int,
    maximum_minutes: int | None = None,
) -> datetime:
    minimum = max(1, minimum_minutes)
    maximum = max(minimum, maximum_minutes or minimum)
    if current > now:
        return current
    return now + timedelta(minutes=random.randint(minimum, maximum))


def enqueue_due_schedules(db, now: datetime | None = None, limit: int = 20) -> int:
    now = now or datetime.utcnow()
    rules = (
        db.query(Rule)
        .filter(
            Rule.enabled.is_(True),
            Rule.match_mode == "schedule",
            Rule.schedule_next_run_at.is_not(None),
            Rule.schedule_next_run_at <= now,
        )
        .order_by(Rule.schedule_next_run_at.asc(), Rule.id.asc())
        .limit(limit)
        .all()
    )
    created = 0
    for rule in rules:
        current_run = rule.schedule_next_run_at or now
        minimum_minutes = rule.schedule_interval_min_minutes or rule.schedule_interval_minutes or 120
        maximum_minutes = rule.schedule_interval_max_minutes or rule.schedule_interval_minutes or minimum_minutes
        rule.schedule_next_run_at = next_schedule_time(
            current_run,
            now,
            minimum_minutes,
            maximum_minutes,
        )

        targets = db.query(RuleScheduleTarget).filter(RuleScheduleTarget.rule_id == rule.id).all()
        if targets:
            target_pairs = [(target.account_id, target.chat_id) for target in targets]
        elif rule.schedule_account_id and rule.schedule_chat_id:
            target_pairs = [(rule.schedule_account_id, rule.schedule_chat_id)]
        else:
            target_pairs = []
        if not target_pairs or not rule.reply_template.strip():
            continue

        for account_id, chat_id in target_pairs:
            account = db.get(Account, account_id)
            chat = db.get(Chat, chat_id)
            if not account or not chat or chat.account_id != account.id or not chat.is_available:
                continue

            outstanding = (
                db.query(SendQueue.id)
                .filter(
                    SendQueue.rule_id == rule.id,
                    SendQueue.account_id == account.id,
                    SendQueue.destination_chat_id == chat.telegram_chat_id,
                    SendQueue.send_type == SEND_MODE_SCHEDULED_GROUP,
                    SendQueue.status.in_([QUEUE_PENDING, QUEUE_SENDING, QUEUE_FLOOD_WAIT]),
                )
                .first()
            )
            if outstanding:
                continue

            context = MessageContext(
                account_id=account.id,
                chat_id=chat.id,
                telegram_chat_id=chat.telegram_chat_id,
                message_id=None,
                telegram_user_id=None,
                username=None,
                text="",
            )
            db.add(
                SendQueue(
                    account_id=account.id,
                    rule_id=rule.id,
                    lead_id=None,
                    telegram_user_id=None,
                    destination_chat_id=chat.telegram_chat_id,
                    reply_to_message_id=None,
                    message_text=render_template(rule.reply_template, context, rule),
                    send_type=SEND_MODE_SCHEDULED_GROUP,
                    status=QUEUE_PENDING,
                    scheduled_at=now,
                )
            )
            created += 1
    return created


class TelegramWorker:
    
    def __init__(self, account_id: int):
        self.account_id = account_id
        self.client = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"telegram-worker-{self.account_id}")

    async def stop(self) -> None:
        self._stop.set()
        if self.client:
            with contextlib.suppress(Exception):
                await self.client.disconnect()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError):
                self._task.cancel()
                await self._task

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with session_scope() as db:
                    account = db.get(Account, self.account_id)
                    if not account or account.status != ACCOUNT_STATUS_ACTIVE:
                        return
                    session_string = decrypt_text(account.session_string_encrypted)
                    self.client = make_client(account, session_string=session_string)

                await self.client.connect()
                if not await self.client.is_user_authorized():
                    self._mark_status(ACCOUNT_STATUS_LOGIN_REQUIRED, "session is not authorized")
                    return
                self._mark_healthy()
                
                # Use the high-level event so Telethon normalizes chat IDs and senders.
                @self.client.on(events.NewMessage(incoming=True))
                async def handler(event):
                    asyncio.create_task(self._handle_message(event))
                
                await self.client.run_until_disconnected()
            except UserDeactivatedBanError as exc:
                self._mark_status(ACCOUNT_STATUS_BANNED, str(exc))
                return
            except AuthKeyUnregisteredError as exc:
                self._mark_status(ACCOUNT_STATUS_LOGIN_REQUIRED, str(exc))
                return
            except FloodWaitError as exc:
                self._mark_flood_wait(exc.seconds, str(exc))
                await asyncio.sleep(min(exc.seconds, 300))
            except OSError as exc:
                self._mark_status(ACCOUNT_STATUS_PROXY_ERROR, str(exc))
                await asyncio.sleep(30)
            except Exception as exc:
                print(f"[ERROR] Worker exception: {exc}")
                self._mark_status(ACCOUNT_STATUS_LIMITED, str(exc))
                await asyncio.sleep(30)

    def _mark_status(self, status: str, error: str | None = None) -> None:
        with session_scope() as db:
            account = db.get(Account, self.account_id)
            if account:
                account.status = status
                account.last_error = error
                account.last_health_check_at = datetime.utcnow()

    def _mark_healthy(self) -> None:
        with session_scope() as db:
            account = db.get(Account, self.account_id)
            if account:
                account.last_health_check_at = datetime.utcnow()
                account.last_error = None

    def _mark_flood_wait(self, seconds: int, error: str) -> None:
        with session_scope() as db:
            account = db.get(Account, self.account_id)
            if account:
                account.status = ACCOUNT_STATUS_FLOOD_WAIT
                account.flood_wait_until = datetime.utcnow() + timedelta(seconds=seconds)
                account.last_error = error
            (
                db.query(SendQueue)
                .filter(SendQueue.account_id == self.account_id, SendQueue.status.in_([QUEUE_PENDING, QUEUE_SENDING]))
                .update({"status": QUEUE_FLOOD_WAIT, "error": error})
            )

    # async def _on_connected(self) -> None:
    #     from app.config import get_settings
    #     settings = get_settings()
    #     if not settings.health_check_chat_ids:
    #         return
    #     chat_ids = [int(x.strip()) for x in settings.health_check_chat_ids.split(",") if x.strip()]
    #     if not chat_ids:
    #         return
    #     dialogs = await self.client.get_dialogs()
    #     dialog_ids = {int(d.id) for d in dialogs}
    #     missing = [cid for cid in chat_ids if cid not in dialog_ids]
    #     if missing:
    #         with session_scope() as db:
    #             account = db.get(Account, self.account_id)
    #             if account:
    #                 account.risk_status = f"not_in_health_check_chat: {','.join(str(x) for x in missing)}"
    #         return
    #     all_ok = True
    #     for chat_id in chat_ids:
    #         try:
    #             await self.client.send_message(chat_id, "online")
    #         except Exception:
    #             all_ok = False
    #     with session_scope() as db:
    #         account = db.get(Account, self.account_id)
    #         if account:
    #             account.risk_status = "ok" if all_ok else "health_check_failed"

    async def _handle_message(self, event: Any) -> None:
        """处理消息事件（备用）"""
        try:
            text = event.raw_text or ""
            if not text.strip():
                return
            
            try:
                sender = await event.get_sender()
            except Exception:
                sender = None
            
            username = getattr(sender, "username", None) if sender else None
            user_id = getattr(sender, "id", None) if sender else None
            telegram_chat_id = int(event.chat_id)
            message_id = getattr(event.message, "id", None)
            
            print(f"[MSG] {telegram_chat_id}|{text[:30]}")
            

            # 数据库处理
            with session_scope() as db:
                chat = (
                    db.query(Chat)
                    .filter(
                        Chat.account_id == self.account_id,
                        Chat.telegram_chat_id == telegram_chat_id,
                        Chat.enabled.is_(True),
                        Chat.is_available.is_(True),
                        Chat.is_primary_listener.is_(True),
                    )
                    .first()
                )
                if not chat:
                    return
                
                ctx = MessageContext(
                    account_id=self.account_id,
                    chat_id=chat.id,
                    telegram_chat_id=telegram_chat_id,
                    message_id=message_id,
                    telegram_user_id=user_id,
                    username=username,
                    text=text,
                )
                process_incoming_message(db, ctx)
        except Exception as e:
            print(f"[ERROR] {e}")


class WorkerManager:
    def __init__(self):
        self.workers: dict[int, TelegramWorker] = {}
        self.queue_task: asyncio.Task | None = None
        self.health_task: asyncio.Task | None = None
        self.running = False

    async def start(self) -> None:
        self.running = True
        await self.reload_workers()
        if not self.queue_task or self.queue_task.done():
            self.queue_task = asyncio.create_task(self.queue_loop(), name="send-queue-loop")
        if not self.health_task or self.health_task.done():
            self.health_task = asyncio.create_task(self.health_loop(), name="health-loop")

    async def stop(self) -> None:
        self.running = False
        for task in [self.queue_task, self.health_task]:
            if task:
                task.cancel()
        await asyncio.gather(*(worker.stop() for worker in list(self.workers.values())), return_exceptions=True)
        self.workers.clear()

    async def reload_workers(self) -> None:
        with session_scope() as db:
            active_ids = {row[0] for row in db.query(Account.id).filter(Account.status == ACCOUNT_STATUS_ACTIVE).all()}
            retained_ids = {
                row[0]
                for row in db.query(Account.id)
                .filter(Account.status.in_([ACCOUNT_STATUS_ACTIVE, ACCOUNT_STATUS_FLOOD_WAIT]))
                .all()
            }
        for account_id in active_ids:
            current = self.workers.get(account_id)
            if not current or not current._task or current._task.done():
                worker = TelegramWorker(account_id)
                self.workers[account_id] = worker
                await worker.start()
        for account_id in list(self.workers.keys()):
            if account_id not in retained_ids:
                await self.workers.pop(account_id).stop()

    async def health_loop(self) -> None:
        from app.config import get_settings
        while self.running:
            with session_scope() as db:
                recover_expired_flood_waits(db, datetime.utcnow())
                stale_cutoff = datetime.utcnow() - timedelta(minutes=10)
                db.query(SendQueue).filter(
                    SendQueue.status == QUEUE_SENDING,
                    SendQueue.updated_at < stale_cutoff,
                ).update(
                    {"status": QUEUE_PENDING, "error": "recovered from stale sending"},
                    synchronize_session=False,
                )
            await self.reload_workers()
            await asyncio.sleep(get_settings().health_check_seconds)

    async def queue_loop(self) -> None:
        from app.config import get_settings
        while self.running:
            with session_scope() as db:
                enqueue_due_schedules(db, datetime.utcnow())
            await self.process_pending_queue(limit=get_settings().queue_batch_size)
            await asyncio.sleep(get_settings().queue_poll_seconds)

    async def process_pending_queue(self, limit: int = 10) -> None:
        from app.config import get_settings

        with session_scope() as db:
            now = datetime.utcnow()
            max_attempts = max(1, get_settings().outbound_max_attempts)
            exhausted = db.query(SendQueue).filter(
                SendQueue.status == QUEUE_PENDING,
                SendQueue.attempts >= max_attempts,
            )
            exhausted.update(
                {"status": QUEUE_PAUSED, "error": "maximum send attempts reached"},
                synchronize_session=False,
            )

            account_ids = [
                row[0]
                for row in db.query(Account.id)
                .filter(Account.status == ACCOUNT_STATUS_ACTIVE, Account.send_enabled.is_(True))
                .all()
            ]
            jobs = []
            for account_id in account_ids:
                job = (
                    db.query(SendQueue)
                    .filter(
                        SendQueue.account_id == account_id,
                        SendQueue.status == QUEUE_PENDING,
                        SendQueue.scheduled_at <= now,
                        SendQueue.attempts < max_attempts,
                    )
                    .order_by(SendQueue.scheduled_at.asc(), SendQueue.created_at.asc())
                    .first()
                )
                if job:
                    jobs.append(job)
            jobs = sorted(jobs, key=lambda job: (job.scheduled_at, job.created_at))[:limit]
            job_ids = [job.id for job in jobs]
            for job in jobs:
                job.status = QUEUE_SENDING
                job.attempts += 1
        for job_id in job_ids:
            await self._send_job(job_id)

    async def _send_job(self, job_id: int) -> None:
        with session_scope() as db:
            job = db.get(SendQueue, job_id)
            if not job or job.status != QUEUE_SENDING:
                return
            account = db.get(Account, job.account_id)
            if not account or not account.send_enabled:
                job.status = QUEUE_PAUSED
                job.error = "account is missing or send is disabled"
                return
            if account.status == ACCOUNT_STATUS_FLOOD_WAIT:
                job.status = QUEUE_FLOOD_WAIT
                job.scheduled_at = account.flood_wait_until or datetime.utcnow() + timedelta(minutes=5)
                job.error = account.last_error or "account is waiting after FloodWait"
                return
            if account.status != ACCOUNT_STATUS_ACTIVE:
                job.status = QUEUE_PAUSED
                job.error = f"account status blocks sending: {account.status}"
                return

            from app.config import get_settings

            last_sent_at = (
                db.query(func.max(SendLog.created_at))
                .filter(SendLog.account_id == account.id, SendLog.status == QUEUE_SENT)
                .scalar()
            )
            minimum_interval = max(0, get_settings().outbound_min_interval_seconds)
            next_allowed_at = last_sent_at + timedelta(seconds=minimum_interval) if last_sent_at else None
            if next_allowed_at and next_allowed_at > datetime.utcnow():
                job.status = QUEUE_PENDING
                job.scheduled_at = next_allowed_at
                job.attempts = max(0, job.attempts - 1)
                job.error = None
                return
            account_id = account.id
            destination = job.destination_chat_id
            text = job.message_text
            reply_to = job.reply_to_message_id
            send_type = job.send_type

        worker = self.workers.get(account_id)
        if not worker or not worker.client:
            with session_scope() as db:
                job = db.get(SendQueue, job_id)
                if job:
                    job.status = QUEUE_FAILED
                    job.error = "worker is not running"
            return

        try:
            if send_type == "group_reply" and reply_to:
                sent = await worker.client.send_message(destination, text, reply_to=reply_to)
            else:
                sent = await worker.client.send_message(destination, text)
            with session_scope() as db:
                job = db.get(SendQueue, job_id)
                if job:
                    job.status = QUEUE_SENT
                    job.sent_at = datetime.utcnow()
                    db.add(SendLog(
                        queue_id=job.id,
                        account_id=job.account_id,
                        rule_id=job.rule_id,
                        chat_id=job.destination_chat_id,
                        user_id=job.telegram_user_id,
                        message_id=getattr(sent, "id", None),
                        status=QUEUE_SENT,
                    ))
        except FloodWaitError as exc:
            with session_scope() as db:
                wait_until = datetime.utcnow() + timedelta(seconds=exc.seconds)
                account = db.get(Account, account_id)
                if account:
                    account.status = ACCOUNT_STATUS_FLOOD_WAIT
                    account.flood_wait_until = wait_until
                    account.last_error = str(exc)
                (
                    db.query(SendQueue)
                    .filter(
                        SendQueue.account_id == account_id,
                        SendQueue.status.in_([QUEUE_PENDING, QUEUE_SENDING]),
                    )
                    .update(
                        {"status": QUEUE_FLOOD_WAIT, "scheduled_at": wait_until, "error": str(exc)},
                        synchronize_session=False,
                    )
                )
                job = db.get(SendQueue, job_id)
                if job:
                    if job.attempts >= max(1, get_settings().outbound_max_attempts):
                        job.status = QUEUE_PAUSED
                        job.error = f"repeated FloodWait; manual review required: {exc}"
                    db.add(SendLog(queue_id=job.id, account_id=job.account_id, rule_id=job.rule_id, chat_id=job.destination_chat_id, user_id=job.telegram_user_id, status=QUEUE_FLOOD_WAIT, error=str(exc)))
        except Exception as exc:
            with session_scope() as db:
                job = db.get(SendQueue, job_id)
                if job:
                    from app.config import get_settings

                    if job.attempts >= max(1, get_settings().outbound_max_attempts):
                        job.status = QUEUE_FAILED
                    else:
                        job.status = QUEUE_PENDING
                        job.scheduled_at = datetime.utcnow() + timedelta(seconds=retry_delay_seconds(job.attempts))
                    job.error = str(exc)
                    db.add(SendLog(queue_id=job.id, account_id=job.account_id, rule_id=job.rule_id, chat_id=job.destination_chat_id, user_id=job.telegram_user_id, status=QUEUE_FAILED, error=str(exc)))


manager = WorkerManager()


async def send_login_code(account_id: int) -> None:
    with session_scope() as db:
        account = db.get(Account, account_id)
        if not account:
            raise ValueError("account not found")
        client = make_client(account)
        phone = account.phone
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        session_string = StringSession.save(client.session)
        with session_scope() as db:
            account = db.get(Account, account_id)
            account.phone_code_hash = sent.phone_code_hash
            account.login_temp_session_string_encrypted = encrypt_text(session_string)
            account.status = ACCOUNT_STATUS_LOGIN_REQUIRED
            account.last_error = None
    finally:
        await client.disconnect()


async def verify_login_code(account_id: int, code: str, password: str | None = None) -> None:
    with session_scope() as db:
        account = db.get(Account, account_id)
        if not account:
            raise ValueError("account not found")
        temp_session = decrypt_text(account.login_temp_session_string_encrypted)
        client = make_client(account, session_string=temp_session)
        phone = account.phone
        phone_code_hash = account.phone_code_hash
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                raise ValueError("two-step password is required")
            await client.sign_in(password=password)
        session_string = StringSession.save(client.session)
        with session_scope() as db:
            account = db.get(Account, account_id)
            account.session_string_encrypted = encrypt_text(session_string)
            account.login_temp_session_string_encrypted = None
            account.phone_code_hash = None
            account.status = ACCOUNT_STATUS_ACTIVE
            account.last_error = None
    finally:
        await client.disconnect()


async def sync_account_chats(account_id: int) -> int:
    with session_scope() as db:
        account = db.get(Account, account_id)
        if not account:
            raise ValueError("account not found")
        session_string = decrypt_text(account.session_string_encrypted)
        client = make_client(account, session_string=session_string)
    count = 0
    await client.connect()
    try:
        if not await client.is_user_authorized():
            with session_scope() as db:
                account = db.get(Account, account_id)
                account.status = ACCOUNT_STATUS_LOGIN_REQUIRED
            return 0
        dialogs = await client.get_dialogs()
        seen_chat_ids: set[int] = set()
        with session_scope() as db:
            for dialog in dialogs:
                entity = dialog.entity
                telegram_chat_id = int(dialog.id)
                if getattr(entity, "broadcast", False):
                    chat_type = "channel"
                elif getattr(entity, "megagroup", False):
                    chat_type = "supergroup"
                elif getattr(entity, "participants_count", None) is not None:
                    chat_type = "group"
                else:
                    continue
                seen_chat_ids.add(telegram_chat_id)
                existing = db.query(Chat).filter(Chat.account_id == account_id, Chat.telegram_chat_id == telegram_chat_id).first()
                if not existing:
                    existing = Chat(account_id=account_id, telegram_chat_id=telegram_chat_id, title=dialog.name or str(telegram_chat_id), type=chat_type)
                    db.add(existing)
                existing.title = dialog.name or existing.title
                existing.type = chat_type
                existing.is_available = True
                existing.last_sync_at = datetime.utcnow()
                count += 1
            reconcile_account_chats(db, account_id, seen_chat_ids)
            _assign_primary_listeners(db)
    finally:
        await client.disconnect()
    return count


def reconcile_account_chats(db, account_id: int, seen_chat_ids: set[int], synced_at: datetime | None = None) -> int:
    """Disable stale chat rows that Telegram no longer returns for this account."""
    synced_at = synced_at or datetime.utcnow()
    stale_chats = (
        db.query(Chat)
        .filter(Chat.account_id == account_id, ~Chat.telegram_chat_id.in_(seen_chat_ids or {-1}), Chat.is_available.is_(True))
        .all()
    )
    for chat in stale_chats:
        chat.is_available = False
        chat.enabled = False
        chat.is_primary_listener = False
        chat.last_sync_at = synced_at
    return len(stale_chats)


def _assign_primary_listeners(db) -> None:
    chat_ids = [
        row[0]
        for row in db.query(Chat.telegram_chat_id)
        .filter(Chat.enabled.is_(True), Chat.is_available.is_(True))
        .distinct()
        .all()
    ]
    for telegram_chat_id in chat_ids:
        chats = (
            db.query(Chat)
            .join(Account, Account.id == Chat.account_id)
            .filter(
                Chat.telegram_chat_id == telegram_chat_id,
                Chat.enabled.is_(True),
                Chat.is_available.is_(True),
            )
            .order_by((Account.status == ACCOUNT_STATUS_ACTIVE).desc(), Chat.id.asc())
            .all()
        )
        for index, chat in enumerate(chats):
            chat.is_primary_listener = index == 0
