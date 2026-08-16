VALID_MATCH_MODES = {"keyword", "regex"}
VALID_MATCH_MODES.add("schedule")
VALID_SEND_MODES = {"record_only", "group_reply", "private_message", "both", "scheduled_group"}


def reply_flags(send_mode: str) -> tuple[bool, bool]:
    if send_mode not in VALID_SEND_MODES:
        raise ValueError("invalid send mode")
    if send_mode == "scheduled_group":
        return False, False
    return send_mode in {"group_reply", "both"}, send_mode in {"private_message", "both"}


def validate_rule_modes(match_mode: str, send_mode: str) -> None:
    if match_mode not in VALID_MATCH_MODES:
        raise ValueError("invalid match mode")
    reply_flags(send_mode)
    if match_mode == "schedule" and send_mode != "scheduled_group":
        raise ValueError("scheduled rules must use scheduled_group")
    if match_mode != "schedule" and send_mode == "scheduled_group":
        raise ValueError("scheduled_group requires schedule mode")
