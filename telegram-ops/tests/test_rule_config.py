from datetime import datetime

import pytest

from app.rule_config import reply_flags, validate_rule_modes
from app.presentation import display_datetime, display_label


@pytest.mark.parametrize(
    ("send_mode", "expected"),
    [
        ("record_only", (False, False)),
        ("group_reply", (True, False)),
        ("private_message", (False, True)),
        ("both", (True, True)),
        ("scheduled_group", (False, False)),
    ],
)
def test_reply_flags_follow_send_mode(send_mode, expected):
    assert reply_flags(send_mode) == expected


def test_invalid_rule_modes_are_rejected():
    with pytest.raises(ValueError):
        validate_rule_modes("unknown", "record_only")
    with pytest.raises(ValueError):
        validate_rule_modes("keyword", "unknown")
    with pytest.raises(ValueError):
        validate_rule_modes("schedule", "group_reply")
    with pytest.raises(ValueError):
        validate_rule_modes("keyword", "scheduled_group")
    validate_rule_modes("schedule", "scheduled_group")


def test_listening_labels_are_human_readable():
    assert display_label("record_only") == "仅记录"
    assert display_label("new") == "新监听"
    assert display_label("schedule") == "定时任务"
    assert display_label("scheduled_group") == "定时群发"


def test_display_datetime_converts_utc_to_beijing_time():
    assert display_datetime(datetime(2026, 8, 16, 3, 13, 0, 89666)) == "2026-08-16 11:13:00"
    assert display_datetime(None) == "-"
