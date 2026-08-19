from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import json
import random

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import admin_password_ok, client_ip_allowed, create_session_token, current_admin
from app.account_transfer import AccountImportError, MAX_IMPORT_BYTES, export_accounts, import_accounts
from app.config import get_settings
from app.crypto import encrypt_text
from app.database import get_db, init_db, session_scope
from app.enums import ACCOUNT_STATUS_DISABLED, QUEUE_PAUSED, QUEUE_PENDING, SEND_MODE_SCHEDULED_GROUP
from app.models import Account, Chat, Lead, ReplyTemplate, Rule, RuleScheduleTarget, SendLog, SendQueue, UserGuard
from app.presentation import boolean_label, display_datetime, display_label
from app.rule_config import reply_flags, validate_rule_modes
from app.workers import manager, send_login_code, sync_account_chats, verify_login_code


settings = get_settings()
templates = Jinja2Templates(directory=str(settings.templates_dir))


def worker_status() -> dict[str, object]:
    online_count = sum(
        1
        for worker in manager.workers.values()
        if worker._task and not worker._task.done()
    )
    return {"running": manager.running, "count": online_count}


templates.env.globals["worker_status"] = worker_status
templates.env.globals["display_label"] = display_label
templates.env.globals["boolean_label"] = boolean_label
templates.env.globals["display_datetime"] = display_datetime


NOTICE_MESSAGES = {
    "account_saved": "账户已保存。",
    "account_updated": "账户修改已保存。",
    "account_toggled": "账户状态已更新。",
    "login_success": "Telegram 账号登录成功。",
    "chats_synced": "群组同步完成。",
    "chat_toggled": "监听状态已更新。",
    "chat_unavailable": "该群组已被同步标记为不可用，请重新加入后同步群组。",
    "rule_saved": "规则已保存。",
    "rule_updated": "规则修改已保存。",
    "rule_toggled": "规则状态已更新。",
    "worker_started": "Worker 已启动。",
    "worker_stopped": "Worker 已停止。",
    "guard_saved": "黑名单/退订设置已保存。",
}


def notice_message(request: Request) -> str | None:
    code = request.query_params.get("notice")
    if code == "accounts_imported":
        created = request.query_params.get("created", "0")
        updated = request.query_params.get("updated", "0")
        return f"账号导入完成：新增 {created} 个，覆盖 {updated} 个。"
    return NOTICE_MESSAGES.get(code)


templates.env.globals["notice_message"] = notice_message


def normalize_rule_delivery_flags() -> None:
    with session_scope() as db:
        for rule in db.query(Rule).all():
            try:
                group_reply, private_message = reply_flags(rule.send_mode)
            except ValueError:
                continue
            rule.group_reply_enabled = group_reply
            rule.private_message_enabled = private_message


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    normalize_rule_delivery_flags()
    if settings.auto_start_telegram_workers:
        await manager.start()
    yield
    await manager.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")


PUBLIC_PATH_PREFIXES = ("/static", "/admin/login", "/favicon.ico")


@app.middleware("http")
async def require_admin_auth(request: Request, call_next):
    path = request.url.path
    client_ip = request.client.host if request.client else None
    if not client_ip_allowed(client_ip):
        return JSONResponse({"detail": "ip is not allowed"}, status_code=403)
    if path in ("/healthz",) or any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES):
        return await call_next(request)
    if current_admin(request):
        return await call_next(request)
    if "text/html" in request.headers.get("accept", "") or request.method == "GET":
        next_url = str(request.url.path)
        if request.url.query:
            next_url += "?" + request.url.query
        return RedirectResponse(f"/admin/login?next={next_url}", status_code=303)
    return JSONResponse({"detail": "admin authentication required"}, status_code=401)


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, next: str = "/"):
    if current_admin(request):
        return redirect(next or "/")
    return templates.TemplateResponse("admin_login.html", {"request": request, "next": next, "error": None})


@app.post("/admin/login")
def admin_login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    if username != settings.admin_username or not admin_password_ok(password):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "next": next, "error": "用户名或密码错误"},
            status_code=401,
        )
    response = redirect(next or "/")
    response.set_cookie(
        settings.admin_session_cookie,
        create_session_token(username),
        max_age=settings.admin_session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.admin_cookie_secure,
    )
    return response


@app.get("/admin/logout")
def admin_logout():
    response = redirect("/admin/login")
    response.delete_cookie(settings.admin_session_cookie)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    data = {
        "accounts": db.query(Account).count(),
        "active_accounts": db.query(Account).filter(Account.status == "active").count(),
        "chats": db.query(Chat).count(),
        "rules": db.query(Rule).count(),
        "leads": db.query(Lead).count(),
        "pending": db.query(SendQueue).filter(SendQueue.status == "pending").count(),
    }
    return templates.TemplateResponse("dashboard.html", {"request": request, "data": data, "worker": worker_status()})


@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, db: Session = Depends(get_db)):
    accounts = db.query(Account).order_by(desc(Account.created_at)).all()
    error_codes = {
        "phone_exists": "这个手机号已经存在，请编辑已有账号。",
        "send_failed": "验证码发送失败，请检查代理地址、端口和网络连接。详情见账号的最近错误。",
        "import_invalid": "导入失败：JSON 文件格式、版本或账号字段无效。",
        "import_too_large": "导入失败：文件不能超过 2 MB。",
    }
    error = error_codes.get(request.query_params.get("error"))
    return templates.TemplateResponse("accounts.html", {"request": request, "accounts": accounts, "error": error})


@app.post("/accounts")
def create_account(
    name: str = Form(""),
    phone: str = Form(...),
    api_id: int = Form(...),
    api_hash: str = Form(...),
    send_enabled: bool = Form(False),
    private_message_enabled: bool = Form(False),
    proxy_enabled: bool = Form(False),
    proxy_type: str = Form("socks5"),
    proxy_host: str = Form(""),
    proxy_port: str = Form(""),
    proxy_username: str = Form(""),
    proxy_password: str = Form(""),
    db: Session = Depends(get_db),
):
    account = Account(
        name=name or phone,
        phone=phone,
        api_id=api_id,
        api_hash_encrypted=encrypt_text(api_hash),
        send_enabled=send_enabled,
        private_message_enabled=private_message_enabled,
        proxy_enabled=proxy_enabled,
        proxy_type=proxy_type or None,
        proxy_host=proxy_host or None,
        proxy_port=int(proxy_port) if proxy_port.strip() else None,
        proxy_username=proxy_username or None,
        proxy_password_encrypted=encrypt_text(proxy_password) if proxy_password else None,
    )
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return redirect("/accounts?error=phone_exists")
    return redirect("/accounts?notice=account_saved")


@app.get("/accounts/export.json")
def accounts_export(db: Session = Depends(get_db)):
    filename = f"telegram-ops-accounts-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    return JSONResponse(
        export_accounts(db),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/accounts/import")
async def accounts_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        return redirect("/accounts?error=import_too_large")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
        result = import_accounts(db, payload)
        db.commit()
    except (AccountImportError, UnicodeDecodeError, json.JSONDecodeError):
        db.rollback()
        return redirect("/accounts?error=import_invalid")
    except Exception:
        db.rollback()
        raise
    await manager.reload_workers()
    return redirect(
        f"/accounts?notice=accounts_imported&created={result['created']}&updated={result['updated']}"
    )


@app.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def account_edit_page(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "account not found")
    return templates.TemplateResponse("account_edit.html", {"request": request, "account": account, "error": None})


@app.post("/accounts/{account_id}/edit")
def account_edit(
    account_id: int,
    name: str = Form(""),
    phone: str = Form(...),
    api_id: int = Form(...),
    api_hash: str = Form(""),
    send_enabled: bool = Form(False),
    private_message_enabled: bool = Form(False),
    proxy_enabled: bool = Form(False),
    proxy_type: str = Form("socks5"),
    proxy_host: str = Form(""),
    proxy_port: str = Form(""),
    proxy_username: str = Form(""),
    proxy_password: str = Form(""),
    db: Session = Depends(get_db),
):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "account not found")
    account.name = name or phone
    account.phone = phone
    account.api_id = api_id
    if api_hash.strip():
        account.api_hash_encrypted = encrypt_text(api_hash.strip())
    account.send_enabled = send_enabled
    account.private_message_enabled = private_message_enabled
    account.proxy_enabled = proxy_enabled
    account.proxy_type = proxy_type or None
    account.proxy_host = proxy_host or None
    account.proxy_port = int(proxy_port) if proxy_port.strip() else None
    account.proxy_username = proxy_username or None
    if proxy_password:
        account.proxy_password_encrypted = encrypt_text(proxy_password)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return redirect("/accounts?error=phone_exists")
    return redirect("/accounts?notice=account_updated")


@app.post("/accounts/{account_id}/toggle")
def toggle_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "account not found")
    account.status = ACCOUNT_STATUS_DISABLED if account.status != ACCOUNT_STATUS_DISABLED else "login_required"
    db.commit()
    return redirect("/accounts?notice=account_toggled")


@app.post("/accounts/{account_id}/send-code")
async def account_send_code(account_id: int, db: Session = Depends(get_db)):
    try:
        await send_login_code(account_id)
    except Exception as exc:
        account = db.get(Account, account_id)
        if account:
            account.last_error = f"{type(exc).__name__}: {exc}"
            db.commit()
        return redirect("/accounts?error=send_failed")
    return redirect(f"/accounts/{account_id}/login")


@app.get("/accounts/{account_id}/login", response_class=HTMLResponse)
def login_page(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "account not found")
    return templates.TemplateResponse("login.html", {"request": request, "account": account, "error": None})


@app.post("/accounts/{account_id}/verify")
async def account_verify(
    account_id: int,
    request: Request,
    code: str = Form(...),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        await verify_login_code(account_id, code, password or None)
    except Exception as exc:
        account = db.get(Account, account_id)
        if not account:
            raise HTTPException(404, "account not found")
        error_messages = {
            "PhoneCodeInvalidError": "验证码错误，请检查后重新输入。",
            "PhoneCodeExpiredError": "验证码已过期，请返回账户页重新发送。",
            "PasswordHashInvalidError": "Telegram 两步验证密码错误。",
            "FloodWaitError": "请求过于频繁，请按 Telegram 指定时间等待后再试。",
        }
        if isinstance(exc, ValueError) and str(exc) == "two-step password is required":
            message = "该账号已开启 Telegram 两步验证，请同时填写二步验证密码。"
        else:
            message = error_messages.get(type(exc).__name__, f"登录失败：{type(exc).__name__}: {exc}")
        account.last_error = message
        db.commit()
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "account": account, "error": message},
            status_code=400,
        )
    await manager.reload_workers()
    return redirect("/accounts?notice=login_success")


@app.post("/accounts/{account_id}/sync-chats")
async def account_sync_chats(account_id: int):
    await sync_account_chats(account_id)
    return redirect("/chats?notice=chats_synced")


@app.post("/workers/start")
async def workers_start():
    await manager.start()
    return redirect("/?notice=worker_started")


@app.post("/workers/stop")
async def workers_stop():
    await manager.stop()
    return redirect("/?notice=worker_stopped")


# @app.post("/accounts/{account_id}/health-check")
# async def account_health_check(account_id: int):
#     """手动触发测活：先同步群组，再检查测活群归属并发测试消息"""
#     await sync_account_chats(account_id)
#     worker = manager.workers.get(account_id)
#     if not worker or not worker.client:
#         raise HTTPException(400, "worker is not running")
#     await worker._on_connected()
#     return redirect("/accounts")


@app.get("/chats", response_class=HTMLResponse)
def chats_page(request: Request, db: Session = Depends(get_db)):
    chats = db.query(Chat).join(Account).order_by(Chat.telegram_chat_id, Chat.account_id).all()
    return templates.TemplateResponse("chats.html", {"request": request, "chats": chats})


@app.post("/chats/{chat_id}/toggle")
def toggle_chat(chat_id: int, db: Session = Depends(get_db)):
    chat = db.get(Chat, chat_id)
    if not chat:
        raise HTTPException(404, "chat not found")
    if not chat.is_available:
        return redirect("/chats?notice=chat_unavailable")
    chat.enabled = not chat.enabled
    db.commit()
    return redirect("/chats?notice=chat_toggled")


def rule_form_context(request: Request, db: Session, **extra: object) -> dict[str, object]:
    context: dict[str, object] = {
        "request": request,
        "accounts": db.query(Account).order_by(Account.name, Account.id).all(),
        "chats": db.query(Chat).join(Account).order_by(Account.name, Chat.title, Chat.id).all(),
        "reply_templates": db.query(ReplyTemplate).order_by(ReplyTemplate.name).all(),
        "schedule_account_ids": [],
        "schedule_chat_ids": [],
        "schedule_interval_min_minutes": 120,
        "schedule_interval_max_minutes": 120,
    }
    rule = extra.get("rule")
    if rule:
        targets = db.query(RuleScheduleTarget).filter(RuleScheduleTarget.rule_id == rule.id).all()
        if targets:
            context["schedule_account_ids"] = sorted({target.account_id for target in targets})
            context["schedule_chat_ids"] = sorted({target.chat_id for target in targets})
        elif rule.schedule_account_id and rule.schedule_chat_id:
            context["schedule_account_ids"] = [rule.schedule_account_id]
            context["schedule_chat_ids"] = [rule.schedule_chat_id]
        context["schedule_interval_min_minutes"] = rule.schedule_interval_min_minutes or rule.schedule_interval_minutes or 120
        context["schedule_interval_max_minutes"] = rule.schedule_interval_max_minutes or rule.schedule_interval_minutes or 120
    context.update(extra)
    return context


def validate_schedule_config(
    db: Session,
    match_mode: str,
    send_mode: str,
    schedule_account_ids: list[int],
    schedule_chat_ids: list[int],
    schedule_interval_min_minutes: str,
    schedule_interval_max_minutes: str,
) -> tuple[list[int], list[int], int, int] | None:
    if match_mode != "schedule":
        return None
    if send_mode != SEND_MODE_SCHEDULED_GROUP:
        raise ValueError("定时任务必须使用“定时群发”发送模式。")
    try:
        account_ids = sorted({int(value) for value in schedule_account_ids})
        chat_ids = sorted({int(value) for value in schedule_chat_ids})
        minimum = int(schedule_interval_min_minutes)
        maximum = int(schedule_interval_max_minutes)
    except (TypeError, ValueError):
        raise ValueError("定时任务需要选择账号、目标群并填写发送间隔。") from None
    if not account_ids or not chat_ids:
        raise ValueError("定时任务至少需要选择一个账号和一个目标群。")
    if any(value <= 0 for value in account_ids + chat_ids):
        raise ValueError("账号和目标群选择无效。")
    if minimum < 1 or maximum < minimum or maximum > 43200:
        raise ValueError("随机间隔范围必须是 1 到 43200 分钟，且最大值不能小于最小值。")
    accounts = db.query(Account).filter(Account.id.in_(account_ids)).all()
    chats = db.query(Chat).filter(Chat.id.in_(chat_ids)).all()
    if len(accounts) != len(account_ids) or len(chats) != len(chat_ids):
        raise ValueError("选择的账号或目标群不存在。")
    account_set = {account.id for account in accounts}
    if any(chat.account_id not in account_set for chat in chats):
        raise ValueError("每个目标群都必须属于已选择的账号。")
    return account_ids, chat_ids, minimum, maximum


def apply_schedule_config(rule: Rule, config: tuple[list[int], list[int], int, int] | None, enabled: bool, db: Session) -> None:
    db.query(RuleScheduleTarget).filter(RuleScheduleTarget.rule_id == rule.id).delete(synchronize_session=False)
    if config is None:
        rule.schedule_account_id = None
        rule.schedule_chat_id = None
        rule.schedule_interval_minutes = None
        rule.schedule_interval_min_minutes = None
        rule.schedule_interval_max_minutes = None
        rule.schedule_next_run_at = None
        return
    account_ids, chat_ids, minimum, maximum = config
    chat_by_id = {chat.id: chat for chat in db.query(Chat).filter(Chat.id.in_(chat_ids)).all()}
    for chat_id in chat_ids:
        chat = chat_by_id[chat_id]
        db.add(RuleScheduleTarget(rule_id=rule.id, account_id=chat.account_id, chat_id=chat.id))
    rule.schedule_account_id = account_ids[0]
    rule.schedule_chat_id = chat_ids[0]
    rule.schedule_interval_minutes = minimum if minimum == maximum else None
    rule.schedule_interval_min_minutes = minimum
    rule.schedule_interval_max_minutes = maximum
    rule.schedule_next_run_at = (
        datetime.utcnow() + timedelta(minutes=random.randint(minimum, maximum)) if enabled else None
    )


def pause_pending_schedule_jobs(db: Session, rule_id: int) -> None:
    db.query(SendQueue).filter(
        SendQueue.rule_id == rule_id,
        SendQueue.send_type == SEND_MODE_SCHEDULED_GROUP,
        SendQueue.status == QUEUE_PENDING,
    ).update(
        {"status": QUEUE_PAUSED, "error": "定时规则已停用或修改"},
        synchronize_session=False,
    )


@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, db: Session = Depends(get_db)):
    rules = db.query(Rule).order_by(desc(Rule.created_at)).all()
    return templates.TemplateResponse("rules.html", rule_form_context(request, db, rules=rules))


@app.post("/rules")
def create_rule(
    name: str = Form(...),
    keywords: str = Form(""),
    match_mode: str = Form("keyword"),
    reply_template: str = Form(""),
    send_mode: str = Form("record_only"),
    cooldown_seconds: int = Form(86400),
    daily_limit: int = Form(20),
    enabled: bool = Form(False),
    schedule_account_ids: list[int] = Form([]),
    schedule_chat_ids: list[int] = Form([]),
    schedule_interval_min_minutes: str = Form("120"),
    schedule_interval_max_minutes: str = Form("120"),
    db: Session = Depends(get_db),
):
    try:
        validate_rule_modes(match_mode, send_mode)
    except ValueError:
        raise HTTPException(400, "invalid rule mode") from None
    if match_mode != "schedule" and not keywords.strip():
        raise HTTPException(400, "关键词或正则不能为空")
    if send_mode != "record_only" and not reply_template.strip():
        raise HTTPException(400, "回复模板不能为空")
    try:
        schedule_config = validate_schedule_config(
            db,
            match_mode,
            send_mode,
            schedule_account_ids,
            schedule_chat_ids,
            schedule_interval_min_minutes,
            schedule_interval_max_minutes,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    group_reply_enabled, private_message_enabled = reply_flags(send_mode)
    rule = Rule(
        name=name,
        keywords=keywords,
        match_mode=match_mode,
        reply_template=reply_template,
        send_mode=send_mode,
        group_reply_enabled=group_reply_enabled,
        private_message_enabled=private_message_enabled,
        cooldown_seconds=cooldown_seconds,
        daily_limit=daily_limit,
        enabled=enabled,
    )
    db.add(rule)
    db.flush()
    apply_schedule_config(rule, schedule_config, enabled, db)
    db.commit()
    return redirect("/rules?notice=rule_saved")


@app.get("/rules/{rule_id}/edit", response_class=HTMLResponse)
def rule_edit_page(rule_id: int, request: Request, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(404, "rule not found")
    return templates.TemplateResponse("rule_edit.html", rule_form_context(request, db, rule=rule, error=None))


@app.post("/rules/{rule_id}/edit")
def rule_edit(
    rule_id: int,
    request: Request,
    name: str = Form(...),
    keywords: str = Form(""),
    match_mode: str = Form("keyword"),
    reply_template: str = Form(""),
    send_mode: str = Form("record_only"),
    cooldown_seconds: int = Form(86400),
    daily_limit: int = Form(20),
    enabled: bool = Form(False),
    schedule_account_ids: list[int] = Form([]),
    schedule_chat_ids: list[int] = Form([]),
    schedule_interval_min_minutes: str = Form("120"),
    schedule_interval_max_minutes: str = Form("120"),
    db: Session = Depends(get_db),
):
    rule = db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(404, "rule not found")
    targets = db.query(RuleScheduleTarget).filter(RuleScheduleTarget.rule_id == rule.id).all()
    previous_account_ids = sorted({target.account_id for target in targets})
    previous_chat_ids = sorted({target.chat_id for target in targets})
    if not targets and rule.schedule_account_id and rule.schedule_chat_id:
        previous_account_ids = [rule.schedule_account_id]
        previous_chat_ids = [rule.schedule_chat_id]
    previous_schedule = (
        rule.match_mode,
        previous_account_ids,
        previous_chat_ids,
        rule.schedule_interval_min_minutes or rule.schedule_interval_minutes or 120,
        rule.schedule_interval_max_minutes or rule.schedule_interval_minutes or 120,
    )
    try:
        validate_rule_modes(match_mode, send_mode)
    except ValueError:
        raise HTTPException(400, "invalid rule mode") from None
    if match_mode != "schedule" and not keywords.strip():
        return templates.TemplateResponse(
            "rule_edit.html",
            rule_form_context(request, db, rule=rule, error="关键词或正则不能为空。"),
            status_code=400,
        )
    if send_mode != "record_only" and not reply_template.strip():
        return templates.TemplateResponse(
            "rule_edit.html",
            rule_form_context(request, db, rule=rule, error="发送模式不是“仅记录线索”时，回复模板不能为空。"),
            status_code=400,
        )
    try:
        schedule_config = validate_schedule_config(
            db,
            match_mode,
            send_mode,
            schedule_account_ids,
            schedule_chat_ids,
            schedule_interval_min_minutes,
            schedule_interval_max_minutes,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            "rule_edit.html",
            rule_form_context(request, db, rule=rule, error=str(exc)),
            status_code=400,
        )
    group_reply_enabled, private_message_enabled = reply_flags(send_mode)
    if previous_schedule[0] == "schedule":
        next_schedule = (
            match_mode,
            *(schedule_config or ([], [], None, None)),
        )
        if not enabled or next_schedule != previous_schedule:
            pause_pending_schedule_jobs(db, rule.id)
    rule.name = name
    rule.keywords = keywords
    rule.match_mode = match_mode
    rule.reply_template = reply_template
    rule.send_mode = send_mode
    rule.group_reply_enabled = group_reply_enabled
    rule.private_message_enabled = private_message_enabled
    rule.cooldown_seconds = max(0, cooldown_seconds)
    rule.daily_limit = max(1, daily_limit)
    rule.enabled = enabled
    apply_schedule_config(rule, schedule_config, enabled, db)
    db.commit()
    return redirect("/rules?notice=rule_updated")


@app.post("/reply-templates/save")
def save_reply_template(name: str = Form(...), content: str = Form(...), db: Session = Depends(get_db)):
    name = name.strip()
    content = content.strip()
    if not name or len(name) > 80 or not content:
        return JSONResponse({"ok": False, "message": "模板名称和内容不能为空。"}, status_code=400)
    item = db.query(ReplyTemplate).filter(func.lower(ReplyTemplate.name) == name.lower()).first()
    if item is None:
        item = ReplyTemplate(name=name, content=content)
        db.add(item)
    else:
        item.name = name
        item.content = content
    db.commit()
    db.refresh(item)
    return {"ok": True, "template": {"id": item.id, "name": item.name, "content": item.content}}


@app.post("/reply-templates/{template_id}/delete")
def delete_reply_template(template_id: int, db: Session = Depends(get_db)):
    item = db.get(ReplyTemplate, template_id)
    if item is None:
        return JSONResponse({"ok": False, "message": "模板不存在。"}, status_code=404)
    db.delete(item)
    db.commit()
    return {"ok": True}


@app.post("/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(404, "rule not found")
    rule.enabled = not rule.enabled
    if rule.match_mode == "schedule":
        if not rule.enabled:
            pause_pending_schedule_jobs(db, rule.id)
        minimum = max(1, rule.schedule_interval_min_minutes or rule.schedule_interval_minutes or 120)
        maximum = max(minimum, rule.schedule_interval_max_minutes or rule.schedule_interval_minutes or minimum)
        rule.schedule_next_run_at = (
            datetime.utcnow() + timedelta(minutes=random.randint(minimum, maximum))
            if rule.enabled else None
        )
    db.commit()
    return redirect("/rules?notice=rule_toggled")


@app.get("/leads", response_class=HTMLResponse)
def leads_page(request: Request, db: Session = Depends(get_db)):
    leads = db.query(Lead).order_by(desc(Lead.created_at)).limit(200).all()
    return templates.TemplateResponse("leads.html", {"request": request, "leads": leads})


@app.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request, db: Session = Depends(get_db)):
    jobs = db.query(SendQueue).order_by(desc(SendQueue.created_at)).limit(200).all()
    return templates.TemplateResponse("queue.html", {"request": request, "jobs": jobs})


@app.get("/guards", response_class=HTMLResponse)
def guards_page(request: Request, db: Session = Depends(get_db)):
    guards = db.query(UserGuard).order_by(desc(UserGuard.updated_at)).limit(200).all()
    return templates.TemplateResponse("guards.html", {"request": request, "guards": guards})


@app.post("/guards")
def upsert_guard(
    telegram_user_id: int = Form(...),
    username: str = Form(""),
    blacklisted: bool = Form(False),
    unsubscribed: bool = Form(False),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = db.query(UserGuard).filter(UserGuard.telegram_user_id == telegram_user_id).first()
    if not guard:
        guard = UserGuard(telegram_user_id=telegram_user_id)
        db.add(guard)
    guard.username = username or None
    guard.blacklisted = blacklisted
    guard.unsubscribed = unsubscribed
    guard.note = note or None
    db.commit()
    return redirect("/guards?notice=guard_saved")


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, db: Session = Depends(get_db)):
    logs = db.query(SendLog).order_by(desc(SendLog.created_at)).limit(200).all()
    return templates.TemplateResponse("logs.html", {"request": request, "logs": logs})


@app.get("/healthz")
def healthz():
    status = worker_status()
    return {"ok": True, "running": status["running"], "workers": list(manager.workers.keys()), "online": status["count"]}
