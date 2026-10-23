"""來源登錄表：必填欄位驗證、審閱有效期、暫停與人工恢復。"""

import re
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.models import Source

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class SourceRegistration(BaseModel):
    """新增來源前必須填寫的資料（SPEC 第二章合規閘門第 1 點）。"""

    id: str
    name: str = Field(min_length=1)
    kind: Literal["flight", "hotel"]
    tier: Literal["A", "B", "C"]
    terms_url: str
    reviewed_at: date
    reviewer: str = Field(min_length=1)
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    # 未填時採設定檔預設值（SOURCE_DEFAULT_MIN_INTERVAL_SECONDS、SOURCE_DEFAULT_DAILY_CALL_LIMIT）
    min_interval_seconds: int = Field(
        default_factory=lambda: get_settings().source_default_min_interval_seconds, ge=1
    )
    daily_call_limit: int = Field(default_factory=lambda: get_settings().source_default_daily_call_limit, ge=1)
    cost_per_call_minor: int = Field(default=0, ge=0)
    monthly_budget_minor: int = Field(default=0, ge=0)
    budget_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ID_PATTERN.match(value):
            raise ValueError("id 只能用小寫英數、底線、連字號")
        return value

    @field_validator("reviewer", "name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不可空白")
        return value.strip()

    @field_validator("terms_url")
    @classmethod
    def _check_terms_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("條款網址必須是 http(s) 網址")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def _normalize_hosts(cls, value: list[str]) -> list[str]:
        hosts = [h.strip().lower() for h in value]
        if any(not h or "/" in h or ":" in h or "*" in h for h in hosts):
            raise ValueError("主機名稱格式錯誤（不可含路徑、連接埠或萬用字元）")
        return hosts

    @field_validator("allowed_paths")
    @classmethod
    def _check_paths(cls, value: list[str]) -> list[str]:
        if any(not p.startswith("/") for p in value):
            raise ValueError("允許路徑必須以 / 開頭")
        return value

    @model_validator(mode="after")
    def _check_tier_rules(self) -> "SourceRegistration":
        if self.reviewed_at > utcnow().date():
            raise ValueError("審閱日期不可在未來")
        if self.tier == "C":
            if self.allowed_hosts or self.allowed_paths:
                raise ValueError("C 級來源不發請求，不可設定允許主機或路徑")
        elif not self.allowed_hosts or not self.allowed_paths:
            raise ValueError("A、B 級來源必須設定允許主機與允許路徑")
        if self.cost_per_call_minor > 0 and self.monthly_budget_minor == 0:
            raise ValueError("有呼叫成本的來源必須設定每月預算")
        return self


def register_source(session: Session, registration: SourceRegistration) -> Source:
    """新增或更新來源；更新時保留暫停狀態，需另外人工恢復。"""
    source = session.get(Source, registration.id)
    if source is None:
        source = Source(id=registration.id, enabled=True)
        session.add(source)
    for field, value in registration.model_dump().items():
        setattr(source, field, value)
    session.flush()
    return source


def review_is_current(source: Source, today: date, max_age_days: int) -> bool:
    return today - source.reviewed_at <= timedelta(days=max_age_days)


def pause_source(session: Session, source_id: str, reason: str, at: datetime) -> None:
    source = session.get(Source, source_id)
    if source is not None and source.paused_at is None:
        source.paused_at = at
        source.paused_reason = reason


def resume_source(session: Session, source_id: str, reviewer: str, reviewed_at: date) -> Source:
    """人工複審後恢復來源，並更新審閱人與審閱日期。"""
    if not reviewer.strip():
        raise ValueError("恢復來源必須填寫複審人")
    source = session.get(Source, source_id)
    if source is None:
        raise LookupError(source_id)
    source.paused_at = None
    source.paused_reason = None
    source.reviewer = reviewer.strip()
    source.reviewed_at = reviewed_at
    return source
