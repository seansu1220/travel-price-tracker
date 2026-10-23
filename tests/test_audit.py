from datetime import timedelta

from sqlalchemy import func, select

from app.compliance.audit import AuditLogger, purge_expired_logs
from app.models import FetchLog
from tests.conftest import START


def test_purge_keeps_one_year_of_logs(session_factory):
    audit = AuditLogger(session_factory)
    for days_ago in (400, 366, 364, 1):
        audit.record(
            source_id="s", method="GET", url="https://x/", decision="allowed",
            robots_verdict="allowed", created_at=START - timedelta(days=days_ago),
        )
    with session_factory.begin() as session:
        assert purge_expired_logs(session, START, 365) == 2
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FetchLog)) == 2
