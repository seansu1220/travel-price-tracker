"""稽核日誌：每筆請求（含被拒絕的）寫入 fetch_logs，保留 1 年。"""

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.models import FetchLog


class AuditLogger:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        *,
        source_id: str,
        method: str,
        url: str,
        decision: str,
        robots_verdict: str,
        created_at: datetime,
        status_code: int | None = None,
        duration_ms: int | None = None,
        cost_minor: int = 0,
        detail: str | None = None,
    ) -> None:
        # 用獨立交易寫入，呼叫端失敗也不會讓稽核紀錄消失
        with self._session_factory.begin() as session:
            session.add(
                FetchLog(
                    source_id=source_id,
                    method=method,
                    url=url,
                    decision=decision,
                    robots_verdict=robots_verdict,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    cost_minor=cost_minor,
                    detail=detail,
                    created_at=created_at,
                )
            )


def purge_expired_logs(session: Session, now: datetime, retention_days: int) -> int:
    """刪除超過保留期限的稽核紀錄，回傳刪除筆數（每日排程執行）。"""
    cutoff = now - timedelta(days=retention_days)
    result = session.execute(delete(FetchLog).where(FetchLog.created_at < cutoff))
    return result.rowcount or 0
