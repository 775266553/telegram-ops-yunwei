from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.account_transfer import EXPORT_FORMAT, EXPORT_VERSION, export_accounts, import_accounts
from app.crypto import decrypt_text, encrypt_text
from app.database import Base
from app.models import Account


def make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_account_export_import_overwrites_and_reencrypts():
    db = make_session()
    account = Account(
        name="旧名称",
        phone="+10000000000",
        api_id=100,
        api_hash_encrypted=encrypt_text("old-hash"),
    )
    db.add(account)
    db.commit()

    result = import_accounts(
        db,
        {
            "format": EXPORT_FORMAT,
            "version": EXPORT_VERSION,
            "accounts": [
                {
                    "name": "迁移账号",
                    "phone": "+10000000000",
                    "api_id": 200,
                    "api_hash": "new-hash",
                    "session_string": "portable-session",
                    "send_enabled": True,
                    "private_message_enabled": True,
                    "proxy": {
                        "enabled": True,
                        "type": "http",
                        "host": "127.0.0.1",
                        "port": 7897,
                        "username": "proxy-user",
                        "password": "proxy-pass",
                    },
                }
            ],
        },
    )
    db.commit()

    assert result == {"created": 0, "updated": 1, "total": 1}
    updated = db.query(Account).one()
    assert updated.name == "迁移账号"
    assert updated.api_hash_encrypted != "new-hash"
    assert decrypt_text(updated.api_hash_encrypted) == "new-hash"
    assert decrypt_text(updated.session_string_encrypted) == "portable-session"
    assert decrypt_text(updated.proxy_password_encrypted) == "proxy-pass"

    exported = export_accounts(db)
    assert exported["format"] == EXPORT_FORMAT
    assert exported["version"] == EXPORT_VERSION
    assert exported["accounts"][0]["api_hash"] == "new-hash"
    assert exported["accounts"][0]["session_string"] == "portable-session"
    assert exported["accounts"][0]["proxy"]["port"] == 7897
