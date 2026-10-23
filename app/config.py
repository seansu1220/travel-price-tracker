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
    robots_cache_ttl_seconds: int = 86400
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
