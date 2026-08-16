from datetime import datetime, timedelta, timezone


BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


MATCH_MODE_LABELS = {
    "keyword": "关键词",
    "regex": "正则表达式",
    "schedule": "定时任务",
}

SEND_MODE_LABELS = {
    "record_only": "仅记录",
    "group_reply": "群内回复",
    "private_message": "自动私信",
    "both": "群内回复并私信",
    "scheduled_group": "定时群发",
}

STATUS_LABELS = {
    "active": "在线",
    "login_required": "需要登录",
    "proxy_error": "代理异常",
    "limited": "受限",
    "flood_wait": "等待解限",
    "banned": "已封禁",
    "disabled": "已禁用",
    "pending": "等待发送",
    "sending": "发送中",
    "sent": "已发送",
    "failed": "发送失败",
    "paused": "已暂停",
    "new": "新监听",
    "queued": "已入队",
    "duplicate": "重复监听",
    "blocked": "已拦截",
    "group_reply": "群内回复",
    "private_message": "自动私信",
    "supergroup": "超级群组",
    "group": "群组",
    "channel": "频道",
}


def display_label(value: object) -> str:
    if value is None:
        return "-"
    text = str(value)
    return MATCH_MODE_LABELS.get(text, SEND_MODE_LABELS.get(text, STATUS_LABELS.get(text, text)))


def boolean_label(value: object) -> str:
    return "是" if bool(value) else "否"


def display_datetime(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return value
    elif isinstance(value, datetime):
        parsed = value
    else:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
