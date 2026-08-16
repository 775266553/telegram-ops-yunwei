import socks
from telethon import TelegramClient
from telethon.sessions import StringSession

from app.crypto import decrypt_text
from app.models import Account


def build_proxy(account: Account):
    if not account.proxy_enabled or not account.proxy_host or not account.proxy_port:
        return None
    proxy_type = (account.proxy_type or "socks5").lower()
    if proxy_type == "socks4":
        sock_type = socks.SOCKS4
    elif proxy_type == "http":
        sock_type = socks.HTTP
    else:
        sock_type = socks.SOCKS5

    password = decrypt_text(account.proxy_password_encrypted)
    if account.proxy_username:
        return (sock_type, account.proxy_host, int(account.proxy_port), True, account.proxy_username, password)
    return (sock_type, account.proxy_host, int(account.proxy_port))


def make_client(account: Account, session_string: str | None = None) -> TelegramClient:
    api_hash = decrypt_text(account.api_hash_encrypted)
    return TelegramClient(
        StringSession(session_string or ""),
        account.api_id,
        api_hash,
        proxy=build_proxy(account),
    )
