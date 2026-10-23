import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import fakeredis
import pytest
import respx
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.compliance.gate import ComplianceGate
from app.compliance.registry import SourceRegistration, register_source
from app.config import Settings
from app.models import Base

ROOT_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
START = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class RecordingAlerter:
    def __init__(self) -> None:
        self.alerts: list[tuple[str, str, str]] = []

    def send(self, level: str, title: str, detail: str) -> None:
        self.alerts.append((level, title, detail))

    def titles(self) -> list[str]:
        return [title for _, title, _ in self.alerts]


def fixture_text(relative: str) -> str:
    return (FIXTURES / relative).read_text(encoding="utf-8")


@pytest.fixture
def session_factory():
    # 預設用 SQLite 記憶體資料庫；設 TEST_DATABASE_URL 可改用 PostgreSQL
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        engine = create_engine(url)
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def redis():
    # 預設用 fakeredis；設 TEST_REDIS_URL 可改用真的 Redis（會清空該資料庫）
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        return fakeredis.FakeRedis()
    client = Redis.from_url(url)
    client.flushdb()
    return client


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def alerter():
    return RecordingAlerter()


@pytest.fixture
def settings():
    return Settings(_env_file=None, crawler_contact_email="ops@example.com")


@pytest.fixture
def http():
    # 未登記的網址一律讓測試失敗，確保測試不會連外
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        yield mock


@pytest.fixture
def gate(session_factory, redis, settings, alerter, clock, http):
    g = ComplianceGate(
        session_factory=session_factory, redis=redis, settings=settings, alerter=alerter, clock=clock
    )
    yield g
    g.close()


def registration(**overrides) -> SourceRegistration:
    fields = dict(
        id="b-site",
        name="測試 B 級網站",
        kind="flight",
        tier="B",
        terms_url="https://b.example.com/terms",
        reviewed_at=date(2026, 10, 1),
        reviewer="Sean",
        allowed_hosts=["b.example.com"],
        allowed_paths=["/fares/"],
        min_interval_seconds=10,
        daily_call_limit=500,
    )
    fields.update(overrides)
    return SourceRegistration(**fields)


@pytest.fixture
def add_source(session_factory):
    def _add(**overrides):
        with session_factory.begin() as session:
            return register_source(session, registration(**overrides))

    return _add
