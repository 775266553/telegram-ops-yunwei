from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

# SQLite 特殊配置
if settings.database_url.startswith("sqlite"):
    connect_args["timeout"] = 30
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
        pool_size=0,
        max_overflow=0,
    )
else:
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True, connect_args=connect_args)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_rule_schedule_schema()


def ensure_rule_schedule_schema() -> None:
    """Add schedule columns for databases created before the schedule feature."""
    inspector = inspect(engine)
    if "rules" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("rules")}
    additions = {
        "schedule_account_id": "INTEGER",
        "schedule_chat_id": "INTEGER",
        "schedule_interval_minutes": "INTEGER",
        "schedule_interval_min_minutes": "INTEGER",
        "schedule_interval_max_minutes": "INTEGER",
        "schedule_next_run_at": "DATETIME",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE rules ADD COLUMN {name} {definition}"))
        connection.execute(
            text(
                "UPDATE rules SET "
                "schedule_interval_min_minutes = COALESCE(schedule_interval_min_minutes, schedule_interval_minutes, 120), "
                "schedule_interval_max_minutes = COALESCE(schedule_interval_max_minutes, schedule_interval_minutes, 120) "
                "WHERE schedule_interval_min_minutes IS NULL OR schedule_interval_max_minutes IS NULL"
            )
        )
        index_names = {index["name"] for index in inspect(connection).get_indexes("rules")}
        if "ix_rules_schedule_due" not in index_names:
            connection.execute(text("CREATE INDEX ix_rules_schedule_due ON rules (match_mode, enabled, schedule_next_run_at)"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
