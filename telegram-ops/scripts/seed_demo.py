import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import init_db, session_scope
from app.models import Rule, UserGuard


def main() -> None:
    init_db()
    with session_scope() as db:
        if not db.query(Rule).filter(Rule.name == "价格线索-仅记录").first():
            db.add(
                Rule(
                    name="价格线索-仅记录",
                    keywords="报价,价格,多少钱,费用",
                    match_mode="keyword",
                    reply_template="",
                    send_mode="record_only",
                    group_reply_enabled=False,
                    private_message_enabled=False,
                    cooldown_seconds=86400,
                    daily_limit=100,
                    enabled=True,
                )
            )
        if not db.query(Rule).filter(Rule.name == "官网链接-群内回复").first():
            db.add(
                Rule(
                    name="官网链接-群内回复",
                    keywords="官网,链接,网址",
                    match_mode="keyword",
                    reply_template="@${username} 你好，可以先看这里：请把你的官网链接放到这个模板里。",
                    send_mode="group_reply",
                    group_reply_enabled=True,
                    private_message_enabled=False,
                    cooldown_seconds=3600,
                    daily_limit=50,
                    enabled=True,
                )
            )
        if not db.query(Rule).filter(Rule.name == "合作推广-自动私信").first():
            db.add(
                Rule(
                    name="合作推广-自动私信",
                    keywords="合作,推广,投放,广告",
                    match_mode="keyword",
                    reply_template="你好 ${username}，看到你提到「${message_text}」，如果方便可以回复我你的需求。",
                    send_mode="private_message",
                    group_reply_enabled=False,
                    private_message_enabled=True,
                    cooldown_seconds=86400,
                    daily_limit=20,
                    enabled=True,
                )
            )
        if not db.query(UserGuard).filter(UserGuard.telegram_user_id == 123456789).first():
            db.add(
                UserGuard(
                    telegram_user_id=123456789,
                    username="demo_blocked_user",
                    blacklisted=True,
                    unsubscribed=False,
                    note="示例黑名单用户",
                )
            )
    print("demo rules and guard records inserted")


if __name__ == "__main__":
    main()
