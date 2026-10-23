"""robots.txt 檢查：每次請求前檢查，結果快取 24 小時（存在 Redis）。

取得 robots.txt 的判定規則（保守原則）：
- 2xx：依內容判定
- 401、403：視為全部禁止
- 其他 4xx（如 404）：視為沒有限制
- 3xx 轉址：依 RFC 9309 跟隨，最多 ROBOTS_MAX_REDIRECTS 次（預設 5），且只限原網域或其子網域；
  轉到其他網域或超過次數，視為無法確認
- 5xx、連線錯誤：視為無法確認，一律不放行（ROBOTS_UNAVAILABLE_RETRY_SECONDS 後再試）
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

import httpx
from redis import Redis


@dataclass(frozen=True)
class RobotsFetchResult:
    status_code: int | None  # None 表示連線失敗
    body: str = ""


# 由閘門提供，確保 robots.txt 本身的請求也經過閘門的 client 與稽核日誌
RobotsFetcher = Callable[[str], RobotsFetchResult]


def robots_url_for(url: str) -> str:
    parsed = httpx.URL(url)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.host}{port}/robots.txt"


def robots_redirect_allowed(origin_host: str, target: httpx.URL) -> bool:
    """robots.txt 轉址目標是否可跟隨：只限 http(s)，且為原網域本身或其子網域。"""
    if target.scheme not in ("http", "https"):
        return False
    origin = origin_host.lower()
    host = target.host.lower()
    return host == origin or host.endswith("." + origin)


class RobotsChecker:
    def __init__(
        self, redis: Redis, fetcher: RobotsFetcher, ttl_seconds: int, unavailable_retry_seconds: int
    ) -> None:
        self._redis = redis
        self._fetcher = fetcher
        self._ttl = ttl_seconds
        self._unavailable_retry = unavailable_retry_seconds

    def verdict(self, url: str, user_agent: str) -> str:
        """回傳 allowed / disallowed / unavailable。"""
        robots_url = robots_url_for(url)
        entry = self._load(robots_url)
        mode = entry["mode"]
        if mode == "allow_all":
            return "allowed"
        if mode == "deny_all":
            return "disallowed"
        if mode == "unavailable":
            return "unavailable"
        parser = RobotFileParser()
        parser.parse(entry["body"].splitlines())
        return "allowed" if parser.can_fetch(user_agent, url) else "disallowed"

    def _load(self, robots_url: str) -> dict:
        key = f"robots:{robots_url}"
        cached = self._redis.get(key)
        if cached is not None:
            return json.loads(cached)
        result = self._fetcher(robots_url)
        entry, ttl = self._classify(result)
        self._redis.set(key, json.dumps(entry), ex=ttl)
        return entry

    def _classify(self, result: RobotsFetchResult) -> tuple[dict, int]:
        status = result.status_code
        if status is not None and 200 <= status < 300:
            return {"mode": "parse", "body": result.body}, self._ttl
        if status in (401, 403):
            return {"mode": "deny_all"}, self._ttl
        if status is not None and 400 <= status < 500:
            return {"mode": "allow_all"}, self._ttl
        return {"mode": "unavailable"}, min(self._ttl, self._unavailable_retry)
