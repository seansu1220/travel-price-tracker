"""可注入的時鐘，方便測試控制時間。"""

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite 會丟掉時區資訊；一律視為 UTC。"""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
