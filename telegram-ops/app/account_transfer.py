from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.crypto import decrypt_text, encrypt_text
from app.enums import ACCOUNT_STATUS_ACTIVE, ACCOUNT_STATUS_LOGIN_REQUIRED
from app.models import Account


EXPORT_FORMAT = "telegram-ops-accounts"
EXPORT_VERSION = 1
MAX_IMPORT_BYTES = 2 * 1024 * 1024


class AccountImportError(ValueError):
    pass


def export_accounts(db: Session) -> dict[str, Any]:
    accounts = db.query(Account).order_by(Account.id).all()
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "accounts": [
            {
                "name": account.name,
                "phone": account.phone,
                "api_id": account.api_id,
                "api_hash": decrypt_text(account.api_hash_encrypted),
                "session_string": decrypt_text(account.session_string_encrypted),
                "send_enabled": account.send_enabled,
                "private_message_enabled": account.private_message_enabled,
                "proxy": {
                    "enabled": account.proxy_enabled,
                    "type": account.proxy_type,
                    "host": account.proxy_host,
                    "port": account.proxy_port,
                    "username": account.proxy_username,
                    "password": decrypt_text(account.proxy_password_encrypted),
                },
            }
            for account in accounts
        ],
    }


def import_accounts(db: Session, payload: object) -> dict[str, int]:
    if not isinstance(payload, dict):
        raise AccountImportError("导入文件必须是 JSON 对象。")
    if payload.get("format") != EXPORT_FORMAT or payload.get("version") != EXPORT_VERSION:
        raise AccountImportError("导入文件格式或版本不受支持。")
    rows = payload.get("accounts")
    if not isinstance(rows, list):
        raise AccountImportError("导入文件缺少 accounts 列表。")

    created = 0
    updated = 0
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise AccountImportError(f"第 {index} 个账号格式错误。")
        phone = str(row.get("phone") or "").strip()
        api_hash = str(row.get("api_hash") or "").strip()
        try:
            api_id = int(row.get("api_id"))
        except (TypeError, ValueError):
            raise AccountImportError(f"第 {index} 个账号的 API ID 无效。") from None
        if not phone or not api_hash or api_id <= 0:
            raise AccountImportError(f"第 {index} 个账号缺少手机号或 API 凭据。")

        proxy = row.get("proxy") or {}
        if not isinstance(proxy, dict):
            raise AccountImportError(f"第 {index} 个账号的代理配置无效。")
        proxy_port = proxy.get("port")
        if proxy_port not in (None, ""):
            try:
                proxy_port = int(proxy_port)
            except (TypeError, ValueError):
                raise AccountImportError(f"第 {index} 个账号的代理端口无效。") from None

        account = db.query(Account).filter(Account.phone == phone).first()
        if account is None:
            account = Account(name=str(row.get("name") or phone), phone=phone, api_id=api_id, api_hash_encrypted="")
            db.add(account)
            created += 1
        else:
            updated += 1

        session_string = row.get("session_string") or None
        account.name = str(row.get("name") or phone).strip() or phone
        account.api_id = api_id
        account.api_hash_encrypted = encrypt_text(api_hash)
        account.session_string_encrypted = encrypt_text(str(session_string)) if session_string else None
        account.login_temp_session_string_encrypted = None
        account.phone_code_hash = None
        account.status = ACCOUNT_STATUS_ACTIVE if session_string else ACCOUNT_STATUS_LOGIN_REQUIRED
        account.risk_status = None
        account.flood_wait_until = None
        account.last_error = None
        account.send_enabled = bool(row.get("send_enabled", True))
        account.private_message_enabled = bool(row.get("private_message_enabled", False))
        account.proxy_enabled = bool(proxy.get("enabled", False))
        account.proxy_type = str(proxy.get("type") or "").strip() or None
        account.proxy_host = str(proxy.get("host") or "").strip() or None
        account.proxy_port = proxy_port
        account.proxy_username = str(proxy.get("username") or "").strip() or None
        proxy_password = proxy.get("password") or None
        account.proxy_password_encrypted = encrypt_text(str(proxy_password)) if proxy_password else None

    return {"created": created, "updated": updated, "total": len(rows)}
