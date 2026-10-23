"""合規稽核日誌：每筆請求（含被閘門拒絕的）都記一筆，保留 1 年。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FetchLog(Base):
    __tablename__ = "fetch_logs"
    __table_args__ = (Index("ix_fetch_logs_source_created", "source_id", "created_at"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    # 不設外鍵：未登錄來源的拒絕紀錄也要留下
    source_id: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(8))
    url: Mapped[str] = mapped_column(Text)
    # 閘門判定：allowed 或 denied:<原因>
    decision: Mapped[str] = mapped_column(String(64))
    # robots 判定：allowed / disallowed / unavailable / not_applicable / not_checked
    robots_verdict: Mapped[str] = mapped_column(String(32))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_minor: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
