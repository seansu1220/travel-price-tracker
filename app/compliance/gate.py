"""合規閘門：系統唯一能對外發 HTTP 請求的地方（SPEC 第二章）。

檢查順序：來源登錄 → 等級 → 審閱期限 → 啟用／暫停 → 網址範圍 → robots.txt（B 級）
→ 限速 → 預算 → 送出 → 封鎖偵測。任何一關不過就拒絕並寫入稽核日誌。
閘門本身絕不重試；收到 403、429、驗證碼頁即暫停來源並告警。
"""

import time
from dataclasses import dataclass
from datetime import datetime

import httpx
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock, as_utc, utcnow
from app.compliance.alerts import Alerter, LoggingAlerter
from app.compliance.audit import AuditLogger
from app.compliance.budget import BudgetGuard
from app.compliance.detection import detect_block
from app.compliance.errors import (
    ComplianceError,
    FetchFailed,
    GateMisconfigured,
    GateUnavailable,
    SourceBlocked,
    SourceDisabled,
    SourceNotRegistered,
    SourcePaused,
    SourceReviewExpired,
    SourceTierC,
    UrlNotAllowed,
    RobotsDisallowed,
)
from app.compliance.limiter import RateLimiter
from app.compliance.registry import pause_source, review_is_current
from app.compliance.robots import RobotsChecker, RobotsFetchResult, robots_redirect_allowed
from app.config import Settings, get_settings
from app.models import Source


@dataclass(frozen=True)
class Permit:
    """通過閘門的許可。瀏覽器配接器（P3）拿到許可後自行載入頁面，再用 report() 回報結果。"""

    source_id: str
    method: str
    url: str
    host: str
    robots_verdict: str
    cost_minor: int
    issued_at: datetime


class ComplianceGate:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        redis: Redis,
        settings: Settings | None = None,
        alerter: Alerter | None = None,
        clock: Clock = utcnow,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        if not self._settings.crawler_contact_email.strip():
            raise GateMisconfigured("未設定 CRAWLER_CONTACT_EMAIL，User-Agent 必須附聯絡信箱")
        self._session_factory = session_factory
        self._redis = redis
        self._alerter = alerter or LoggingAlerter()
        self._clock = clock
        self._audit = AuditLogger(session_factory)
        self._limiter = RateLimiter(redis, clock)
        self._budget = BudgetGuard(redis, clock, self._settings.budget_alert_percent, self._alert_once)
        # 不自動跟隨轉址：轉址目標必須重新經過閘門檢查
        self._client = httpx.Client(
            transport=transport,
            timeout=self._settings.http_timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": self.user_agent},
        )

    @property
    def user_agent(self) -> str:
        settings = self._settings
        contact = settings.crawler_contact_email.strip()
        return f"{settings.crawler_service_name}/{settings.app_version} (+mailto:{contact})"

    # ---- 對外介面 ----

    def request(
        self,
        source_id: str,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        json: object | None = None,
    ) -> httpx.Response:
        """經過全部檢查後送出請求。被拒絕或連線失敗時丟出 ComplianceError 的子類別，絕不重試。"""
        full_url = str(httpx.URL(url, params=params)) if params else url
        permit = self.authorize(source_id, full_url, method)
        safe_headers = {name: value for name, value in (headers or {}).items() if name.lower() != "user-agent"}
        started = time.monotonic()
        try:
            response = self._client.request(method, full_url, headers=safe_headers, json=json)
        except httpx.HTTPError as exc:
            self._log(permit, "error:network", duration_ms=_elapsed_ms(started), detail=repr(exc))
            raise FetchFailed(f"對來源 {source_id} 的請求連線失敗（{method} {full_url}）：{exc!r}") from exc
        self.report(
            permit,
            response.status_code,
            content_type=response.headers.get("content-type", ""),
            body_text=_safe_text(response),
            duration_ms=_elapsed_ms(started),
        )
        return response

    def authorize(self, source_id: str, url: str, method: str = "GET") -> Permit:
        method = method.upper()
        robots_verdict = "not_checked"
        step = "載入來源登錄表"
        try:
            try:
                source = self._load_source(source_id)
                step = "檢查網址範圍"
                host = self._check_url(source, url)
                step = "檢查 robots.txt"
                if source.tier == "B":
                    robots_verdict = self._robots_verdict(source, url)
                else:
                    robots_verdict = "not_applicable"  # A 級依合約授權，不適用 robots.txt
                step = "限速器"
                self._limiter.acquire(host, source.min_interval_seconds)
                step = "預算熔斷"
                self._budget.reserve(source)
            except (RedisError, SQLAlchemyError) as exc:
                raise GateUnavailable(
                    f"合規閘門在「{step}」步驟無法存取 Redis 或資料庫，已拒絕放行（來源 {source_id}）：{exc}"
                ) from exc
        except ComplianceError as exc:
            if isinstance(exc, RobotsDisallowed):
                robots_verdict = exc.verdict
            self._audit.record(
                source_id=source_id,
                method=method,
                url=url,
                decision=f"denied:{exc.code}",
                robots_verdict=robots_verdict,
                created_at=self._clock(),
                detail=str(exc),
            )
            raise
        return Permit(source_id, method, url, host, robots_verdict, source.cost_per_call_minor, self._clock())

    def report(
        self,
        permit: Permit,
        status_code: int,
        *,
        content_type: str = "",
        body_text: str = "",
        duration_ms: int | None = None,
    ) -> None:
        """寫入稽核日誌；若為封鎖回應，暫停來源、告警並丟出 SourceBlocked。"""
        block_reason = detect_block(status_code, content_type, body_text)
        if block_reason is None:
            self._log(permit, "allowed", status_code=status_code, duration_ms=duration_ms)
            return
        # 先暫停、再告警、最後寫日誌：任何一步失敗都不能讓來源繼續被請求而沒人知道
        pause_error = self._pause(permit, block_reason)
        if pause_error is None:
            self._alerter.send(
                "critical",
                f"來源 {permit.source_id} 已暫停",
                f"收到 {block_reason}，網址 {permit.url}。已停止請求，請人工複審後再恢復。",
            )
        else:
            self._alerter.send(
                "critical",
                f"來源 {permit.source_id} 被封鎖但暫停失敗",
                f"收到 {block_reason}，網址 {permit.url}；寫入暫停狀態失敗：{pause_error}。請立即人工停用此來源。",
            )
        self._log(permit, f"blocked:{block_reason}", status_code=status_code, duration_ms=duration_ms)
        raise SourceBlocked(f"{permit.source_id} 回應 {block_reason}，已暫停")

    def close(self) -> None:
        self._client.close()

    def _pause(self, permit: Permit, block_reason: str) -> SQLAlchemyError | None:
        try:
            with self._session_factory.begin() as session:
                pause_source(session, permit.source_id, f"{block_reason}（{permit.url}）", self._clock())
        except SQLAlchemyError as exc:
            return exc
        return None

    # ---- 內部檢查 ----

    def _load_source(self, source_id: str) -> Source:
        with self._session_factory() as session:
            source = session.get(Source, source_id)
        if source is None:
            raise SourceNotRegistered(f"來源 {source_id} 未登錄")
        if source.tier == "C":
            raise SourceTierC(f"來源 {source_id} 為 C 級，只能提供跳轉連結")
        if source.tier not in ("A", "B"):
            raise SourceNotRegistered(f"來源 {source_id} 等級不明：{source.tier}")
        today = self._clock().date()
        if not review_is_current(source, today, self._settings.source_review_max_age_days):
            self._alert_once(
                f"review-expired:{source_id}:{today}",
                "warning",
                f"來源 {source_id} 審閱已過期",
                f"上次審閱 {source.reviewed_at}，超過 {self._settings.source_review_max_age_days} 天",
            )
            raise SourceReviewExpired(f"來源 {source_id} 審閱日期 {source.reviewed_at} 已過期")
        if not source.enabled:
            raise SourceDisabled(f"來源 {source_id} 已停用")
        if source.paused_at is not None:
            raise SourcePaused(f"來源 {source_id} 自 {as_utc(source.paused_at)} 暫停：{source.paused_reason}")
        return source

    def _check_url(self, source: Source, url: str) -> str:
        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL as exc:
            raise UrlNotAllowed(f"網址格式錯誤：{url}") from exc
        if parsed.scheme not in ("https", "http"):
            raise UrlNotAllowed(f"不支援的協定：{parsed.scheme}")
        if parsed.userinfo:
            raise UrlNotAllowed("網址不可帶帳號密碼")
        host = parsed.host.lower()
        if host not in source.allowed_hosts:
            raise UrlNotAllowed(f"主機 {host} 不在來源 {source.id} 的允許清單")
        path = parsed.path or "/"
        if ".." in path.split("/") or "%2e" in parsed.raw_path.decode().lower():
            raise UrlNotAllowed("網址路徑不可含 ..")
        if not any(path.startswith(prefix) for prefix in source.allowed_paths):
            raise UrlNotAllowed(f"路徑 {path} 不在來源 {source.id} 的允許路徑")
        return host

    def _robots_verdict(self, source: Source, url: str) -> str:
        checker = RobotsChecker(
            self._redis,
            lambda robots_url: self._fetch_robots(source.id, robots_url),
            self._settings.robots_cache_ttl_seconds,
            self._settings.robots_unavailable_retry_seconds,
        )
        verdict = checker.verdict(url, self._settings.crawler_service_name)
        if verdict != "allowed":
            self._alert_once(
                f"robots:{source.id}:{httpx.URL(url).path}",
                "warning",
                f"來源 {source.id} 的 robots.txt 不允許",
                f"判定 {verdict}，網址 {url}，已放棄請求",
            )
            raise RobotsDisallowed(f"robots.txt 判定 {verdict}：{url}", verdict=verdict)
        return verdict

    def _fetch_robots(self, source_id: str, robots_url: str) -> RobotsFetchResult:
        """取得 robots.txt，不計入限速與預算（每網域每天約一次），但每一次請求都寫入稽核日誌。

        依 RFC 9309 跟隨轉址，最多 ROBOTS_MAX_REDIRECTS 次，且只限原網域或其子網域；
        否則回傳「無法確認」（status_code=None），閘門不放行。
        """
        origin_host = httpx.URL(robots_url).host
        max_redirects = self._settings.robots_max_redirects
        current_url = robots_url
        for redirects_followed in range(max_redirects + 1):
            response = self._get_robots_once(source_id, current_url)
            if response is None:
                return RobotsFetchResult(status_code=None)
            # 沒有 Location 的 3xx 不跟隨，交給 RobotsChecker 判定為無法確認
            if not (300 <= response.status_code < 400 and "location" in response.headers):
                return RobotsFetchResult(status_code=response.status_code, body=_safe_text(response))
            if redirects_followed == max_redirects:
                break
            target = httpx.URL(current_url).join(response.headers["location"])
            if not robots_redirect_allowed(origin_host, target):
                self._record_robots_redirect_denied(
                    source_id, str(target), f"robots.txt 從 {current_url} 轉址到其他網域，不跟隨"
                )
                return RobotsFetchResult(status_code=None)
            current_url = str(target)
        self._record_robots_redirect_denied(
            source_id, current_url, f"robots.txt 轉址超過 {max_redirects} 次（起點 {robots_url}），不再跟隨"
        )
        return RobotsFetchResult(status_code=None)

    def _get_robots_once(self, source_id: str, url: str) -> httpx.Response | None:
        """送出一次 robots.txt 請求並寫入稽核日誌；連線失敗回傳 None。"""
        started = time.monotonic()
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            self._audit.record(
                source_id=source_id, method="GET", url=url, decision="robots_fetch",
                robots_verdict="not_applicable", created_at=self._clock(),
                duration_ms=_elapsed_ms(started), detail=repr(exc),
            )
            return None
        self._audit.record(
            source_id=source_id, method="GET", url=url, decision="robots_fetch",
            robots_verdict="not_applicable", created_at=self._clock(),
            status_code=response.status_code, duration_ms=_elapsed_ms(started),
        )
        return response

    def _record_robots_redirect_denied(self, source_id: str, url: str, reason: str) -> None:
        self._audit.record(
            source_id=source_id, method="GET", url=url, decision="robots_redirect_denied",
            robots_verdict="unavailable", created_at=self._clock(), detail=reason,
        )

    def _log(self, permit: Permit, decision: str, **fields) -> None:
        self._audit.record(
            source_id=permit.source_id,
            method=permit.method,
            url=permit.url,
            decision=decision,
            robots_verdict=permit.robots_verdict,
            created_at=self._clock(),
            cost_minor=permit.cost_minor,
            **fields,
        )

    def _alert_once(self, dedupe_key: str, level: str, title: str, detail: str) -> None:
        if self._redis.set(f"alert:{dedupe_key}", "1", nx=True, ex=self._settings.alert_dedupe_seconds):
            self._alerter.send(level, title, detail)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _safe_text(response: httpx.Response) -> str:
    try:
        return response.text
    except (UnicodeDecodeError, LookupError):
        return ""
