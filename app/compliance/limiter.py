"""限速器：同一網域兩次請求之間至少間隔 N 秒（Redis 原子操作，多工作者共用）。

間隔以「網域」全域計算，不分來源：多個來源共用同一網域時，任兩次相鄰請求的間隔
必須同時滿足上一次與這一次請求者的設定，也就是取兩者中較長的那個。
"""

from redis import Redis

from app.clock import Clock
from app.compliance.errors import RateLimited

# 鍵值存「上次請求時間|上次請求者要求的間隔」（毫秒）
# 回傳 0 表示放行；否則回傳還要等幾毫秒
_ACQUIRE = """
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local last = redis.call('GET', KEYS[1])
if last then
  local sep = string.find(last, '|', 1, true)
  local last_at = tonumber(string.sub(last, 1, sep - 1))
  local last_interval = tonumber(string.sub(last, sep + 1))
  local required = math.max(interval, last_interval)
  local elapsed = now - last_at
  if elapsed < required then
    return required - elapsed
  end
end
redis.call('SET', KEYS[1], ARGV[1] .. '|' .. ARGV[2], 'PX', interval * 2)
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
