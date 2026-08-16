from app.auth import client_ip_allowed, create_session_token, hash_password, verify_password, verify_session_token
from app.config import get_settings


def test_password_hash_roundtrip():
    stored = hash_password("secret")
    assert verify_password("secret", stored) is True
    assert verify_password("wrong", stored) is False


def test_session_token_roundtrip():
    settings = get_settings()
    token = create_session_token(settings.admin_username)
    assert verify_session_token(token) == settings.admin_username
    assert verify_session_token(token + "tampered") is None


def test_ip_allowlist(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_allowed_ips", "1.2.3.4,10.0.0.0/24")
    assert client_ip_allowed("1.2.3.4") is True
    assert client_ip_allowed("10.0.0.8") is True
    assert client_ip_allowed("8.8.8.8") is False
