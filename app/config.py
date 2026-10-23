"""應用程式設定：一律從環境變數讀取（見 .env.example）。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://tracker:tracker@postgres:5432/tracker"
    redis_url: str = "redis://redis:6379/0"

    crawler_contact_email: str = ""
    crawler_service_name: str = "TravelPriceTrackerBot"
    app_version: str = "0.1.0"

    source_review_max_age_days: int = 180
    # 新來源未指定時的限速預設值（SPEC 第六章：每網域每 10 秒最多 1 次、每日最多 500 次）
    source_default_min_interval_seconds: int = 10
    source_default_daily_call_limit: int = 500
    robots_cache_ttl_seconds: int = 86400
    # robots.txt 無法確認（轉址、5xx、連線失敗）時，多久後再試
    robots_unavailable_retry_seconds: int = 3600
    # 抓 robots.txt 時最多跟隨幾次轉址（RFC 9309 建議至少 5 次）
    robots_max_redirects: int = 5
    # 同一事件的告警，在此秒數內只發一次
    alert_dedupe_seconds: int = 86400
    budget_alert_percent: int = 80
    http_timeout_seconds: float = 20.0
    fetch_log_retention_days: int = 365

    duffel_api_token: str = ""
    travelpayouts_token: str = ""
    telegram_bot_token: str = ""
    admin_telegram_chat_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
