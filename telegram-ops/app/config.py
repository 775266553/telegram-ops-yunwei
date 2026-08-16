from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


APP_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Telegram Ops Workbench"
    app_secret_key: str = Field(default="change-me-at-least-32-chars")
    encryption_key: str = ""
    database_url: str = "sqlite:///./telegram_ops.db"
    auto_start_telegram_workers: bool = False
    config_reload_seconds: int = 10
    health_check_seconds: int = 30
    queue_poll_seconds: int = 3
    queue_batch_size: int = 10
    default_account_daily_limit: int = 20
    default_user_daily_limit: int = 1
    default_user_cooldown_seconds: int = 86400
    outbound_delay_min_seconds: int = 20
    outbound_delay_max_seconds: int = 90
    outbound_min_interval_seconds: int = 45
    outbound_max_attempts: int = 3
    outbound_retry_base_seconds: int = 60
    outbound_retry_max_seconds: int = 1800
    admin_username: str = "admin"
    admin_password: str = "admin123456"
    admin_password_hash: str = ""
    admin_session_cookie: str = "tg_ops_admin"
    admin_session_hours: int = 12
    admin_cookie_secure: bool = False
    admin_allowed_ips: str = ""

    # 测活目标群 ID（逗号分隔），worker 启动时会往这些群发测试消息
    # health_check_chat_ids: str = ""

    templates_dir: Path = APP_ROOT / "templates"
    static_dir: Path = APP_ROOT / "static"


@lru_cache
def get_settings() -> Settings:
    return Settings()
