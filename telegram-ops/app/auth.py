import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import time

from fastapi import Request

from app.config import get_settings


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str, *, iterations: int = 260_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt), int(iterations))
        return hmac.compare_digest(_b64(digest), expected)
    except Exception:
        return False


def admin_password_ok(password: str) -> bool:
    settings = get_settings()
    if settings.admin_password_hash:
        return verify_password(password, settings.admin_password_hash)
    if not settings.admin_password:
        return False
    return secrets.compare_digest(password, settings.admin_password)


def create_session_token(username: str) -> str:
    settings = get_settings()
    expires_at = int(time.time() + settings.admin_session_hours * 3600)
    payload = f"{username}|{expires_at}"
    sig = hmac.new(settings.app_secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{_b64(payload.encode('utf-8'))}.{_b64(sig)}"


def verify_session_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    settings = get_settings()
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64(payload_b64).decode("utf-8")
        expected_sig = hmac.new(settings.app_secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64(expected_sig), sig_b64):
            return None
        username, expires_at = payload.rsplit("|", 1)
        if username != settings.admin_username or int(expires_at) < int(time.time()):
            return None
        return username
    except Exception:
        return None


def current_admin(request: Request) -> str | None:
    settings = get_settings()
    return verify_session_token(request.cookies.get(settings.admin_session_cookie))


def client_ip_allowed(client_ip: str | None) -> bool:
    settings = get_settings()
    rules = [item.strip() for item in settings.admin_allowed_ips.split(",") if item.strip()]
    if not rules:
        return True
    if not client_ip:
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for rule in rules:
        try:
            if "/" in rule:
                if ip in ipaddress.ip_network(rule, strict=False):
                    return True
            elif ip == ipaddress.ip_address(rule):
                return True
        except ValueError:
            continue
    return False
