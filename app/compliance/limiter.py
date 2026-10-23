"""限速器：同一網域兩次請求之間至少間隔 N 秒（Redis 原子操作，多工作者共用）。"""

from redis import Redis

from app.clock import Clock
from app.compliance.errors import RateLimited

# 回傳 0 表示放行；否則回傳還要等幾毫秒
_ACQUIRE = """
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local last = redis.call('GET', KEYS[1])
if last then
  local elapsed = now - tonumber(last)
  if elapsed < interval then
    return interval - elapsed
  end
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', interval * 2)
return 0
"""


class RateLimiter:
    def __init__(self, redis: Redis, clock: Clock) -> None:
        self._script = redis.register_script(_ACQUIRE)
        self._clock = clock

    def acquire(self, host: str, min_interval_seconds: int) -> None:
        now_ms = int(self._clock().timestamp() * 1000)
        wait_ms = int(self._script(keys=[f"ratelimit:host:{host}"], args=[now_ms, min_interval_seconds * 1000]))
        if wait_ms > 0:
            raise RateLimited(f"{host} 需再等 {wait_ms / 1000:.1f} 秒", retry_after_seconds=wait_ms / 1000)
