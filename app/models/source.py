"""來源登錄表：合規閘門的依據（SPEC 第二章、第七章 sources）。"""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("tier IN ('A', 'B', 'C')", name="ck_sources_tier"),
        CheckConstraint("kind IN ('flight', 'hotel')", name="ck_sources_kind"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # flight / hotel
    tier: Mapped[str] = mapped_column(String(1))  # A / B / C
    terms_url: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[date] = mapped_column(Date)
    reviewer: Mapped[str] = mapped_column(String(200))
    # 允許連線的主機與路徑前綴；C 級為空
    allowed_hosts: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 限速：同一網域兩次請求最少間隔秒數、每日呼叫上限
    min_interval_seconds: Mapped[int] = mapped_column(Integer)
    daily_call_limit: Mapped[int] = mapped_column(Integer)
    # 預算：金額以最小貨幣單位存整數
    cost_per_call_minor: Mapped[int] = mapped_column(Integer, default=0)
    monthly_budget_minor: Mapped[int] = mapped_column(Integer, default=0)
    budget_currency: Mapped[str] = mapped_column(String(3), default="USD")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 遇到 403/429/驗證碼時自動暫停，需人工複審後恢復
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
