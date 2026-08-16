import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _derive_key(raw: str) -> bytes:
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    settings = get_settings()
    if settings.encryption_key:
        key = settings.encryption_key.encode("utf-8")
    else:
        key = _derive_key(settings.app_secret_key)
    return Fernet(key)


def encrypt_text(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return value
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return value
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # Keep backward compatibility with early/dev rows that may have been stored as plaintext.
        return value
