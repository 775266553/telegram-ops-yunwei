from pathlib import Path

from app.main import app


def test_account_form_allows_empty_proxy_port():
    route = next(route for route in app.routes if getattr(route, "path", None) == "/accounts" and "POST" in getattr(route, "methods", set()))
    dependant = route.dependant
    field = next(field for field in dependant.body_params if field.name == "proxy_port")
    assert field.field_info.default == ""


def test_account_edit_route_exists():
    route = next(route for route in app.routes if getattr(route, "path", None) == "/accounts/{account_id}/edit" and "POST" in getattr(route, "methods", set()))
    assert route is not None


def test_rule_edit_route_exists():
    route = next(route for route in app.routes if getattr(route, "path", None) == "/rules/{rule_id}/edit" and "POST" in getattr(route, "methods", set()))
    assert route is not None


def test_account_transfer_routes_exist():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/accounts/export.json" in paths
    assert "/accounts/import" in paths


def test_reply_template_routes_exist():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/reply-templates/save" in paths
    assert "/reply-templates/{template_id}/delete" in paths


def test_reply_template_variables_have_descriptions():
    tools = (Path(__file__).parents[1] / "templates" / "_reply_template_tools.html").read_text(encoding="utf-8")
    expected = {
        "$username": "触发消息用户的 Telegram 用户名",
        "$user_id": "触发消息用户的 Telegram 数字 ID",
        "$chat_id": "消息来源群组或频道的 Chat ID",
        "$rule_name": "本次命中的规则名称",
        "$message_text": "用户发送的原始消息",
    }
    for variable, description in expected.items():
        assert variable in tools
        assert description in tools
    assert 'aria-label="插入 {{ variable }}：{{ description }}"' in tools


def test_regex_quick_template_is_available():
    templates_dir = Path(__file__).parents[1] / "templates"
    tools = (templates_dir / "_regex_template_tools.html").read_text(encoding="utf-8")
    rules = (templates_dir / "rules.html").read_text(encoding="utf-8")
    assert "定时 2 小时发一次" in tools
    assert "data-regex-template" in tools
    assert "不是定时器" in tools
    assert "regex-template-row" in tools
    assert "_regex_template_tools.html" in rules


def test_schedule_mode_controls_are_available():
    templates_dir = Path(__file__).parents[1] / "templates"
    static_dir = Path(__file__).parents[1] / "static"
    rules = (templates_dir / "rules.html").read_text(encoding="utf-8")
    edit = (templates_dir / "rule_edit.html").read_text(encoding="utf-8")
    for content in (rules, edit):
        assert 'value="schedule"' in content
        assert 'value="scheduled_group"' in content
        assert 'data-schedule-fields' in content
        assert 'name="schedule_interval_min_minutes"' in content
        assert 'name="schedule_interval_max_minutes"' in content
        assert 'name="schedule_account_ids"' in content
        assert 'name="schedule_chat_ids"' in content
        assert 'type="checkbox"' in content
        assert 'data-schedule-chat-option' in content
        assert '可以直接勾选一个或多个目标群' in content
        assert '<select name="schedule_account_ids"' not in content
    assert "[hidden] { display: none !important; }" in (static_dir / "app.css").read_text(encoding="utf-8")
