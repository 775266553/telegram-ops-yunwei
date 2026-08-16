from types import SimpleNamespace

from datetime import datetime

from app.config import get_settings
from app.rules import MessageContext, initial_scheduled_at, render_template, rule_matches, split_keywords


def test_split_keywords_accepts_newlines_and_commas():
    assert split_keywords("vpn, 代理\nserver，云主机") == ["vpn", "代理", "server", "云主机"]


def test_keyword_rule_matches_case_insensitive():
    rule = SimpleNamespace(enabled=True, match_mode="keyword", keywords="Telegram, FastAPI")
    assert rule_matches(rule, "looking for fastapi panel") is True
    assert rule_matches(rule, "nothing relevant") is False


def test_regex_rule_ignores_invalid_patterns():
    rule = SimpleNamespace(enabled=True, match_mode="regex", keywords="([,hello\\s+world")
    assert rule_matches(rule, "hello world") is True


def test_fixed_interval_regex_template_matches_message_text():
    rule = SimpleNamespace(
        enabled=True,
        match_mode="regex",
        keywords=r"(?:定时|每)\s*2\s*(?:个)?小时\s*(?:发|发送)?\s*一次",
    )
    assert rule_matches(rule, "定时 2 小时发一次") is True
    assert rule_matches(rule, "每 2 个小时发送一次") is True
    assert rule_matches(rule, "每 3 小时发一次") is False


def test_render_template_uses_jinja2():
    rule = SimpleNamespace(name="报价")
    ctx = MessageContext(
        account_id=1,
        chat_id=2,
        telegram_chat_id=100,
        message_id=200,
        telegram_user_id=300,
        username="alice",
        text="想了解价格",
    )
    # Jinja2 语法
    assert render_template("Hi {{ username }}, rule={{ rule_name }}", ctx, rule) == "Hi alice, rule=报价"
    # 兼容旧的 $变量名 语法
    assert render_template("Hi $username, rule=$rule_name", ctx, rule) == "Hi alice, rule=报价"
    # 支持条件判断
    assert render_template("{% if username %}Hello {{ username }}{% endif %}", ctx, rule) == "Hello alice"


def test_initial_schedule_uses_configured_jitter(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "outbound_delay_min_seconds", 10)
    monkeypatch.setattr(settings, "outbound_delay_max_seconds", 20)
    monkeypatch.setattr("app.rules.random.randint", lambda minimum, maximum: maximum)

    now = datetime(2026, 1, 1, 0, 0, 0)
    assert initial_scheduled_at(now) == datetime(2026, 1, 1, 0, 0, 20)
