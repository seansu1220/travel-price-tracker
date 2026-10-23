"""預算熔斷：每個來源每日呼叫上限、每月金額上限。

達告警門檻（預設 80%）發告警；達 100% 拒絕後續請求並告警（降頻由 P1 排程器處理）。
"""

from math import ceil

from redis import Redis

from app.clock import Clock
from app.compliance.errors import BudgetExceeded
from app.models import Source

# 回傳 {狀態, 今日次數, 本月花費}；狀態 0=放行、1=超過每日次數、2=超過每月金額
_RESERVE = """
local daily_limit = tonumber(ARGV[1])
local cost = tonumber(ARGV[2])
local budget = tonumber(ARGV[3])
local calls = tonumber(redis.call('GET', KEYS[1]) or '0')
local spent = tonumber(redis.call('GET', KEYS[2]) or '0')
if calls + 1 > daily_limit then
  return {1, calls, spent}
end
if budget > 0 and spent + cost > budget then
  return {2, calls, spent}
end
calls = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], 172800)
spent = redis.call('INCRBY', KEYS[2], cost)
redis.call('EXPIRE', KEYS[2], 3456000)
return {0, calls, spent}
"""


class BudgetGuard:
    def __init__(self, redis: Redis, clock: Clock, alert_percent: int, notify) -> None:
        self._redis = redis
        self._script = redis.register_script(_RESERVE)
        self._clock = clock
        self._alert_percent = alert_percent
        # notify(dedupe_key, level, title, detail)：由閘門提供，同一事件只告警一次
        self._notify = notify

    def reserve(self, source: Source) -> None:
        now = self._clock()
        day_key = f"budget:calls:{source.id}:{now:%Y%m%d}"
        month_key = f"budget:spent:{source.id}:{now:%Y%m}"
        status, calls, spent = (
            int(v)
            for v in self._script(
                keys=[day_key, month_key],
                args=[source.daily_call_limit, source.cost_per_call_minor, source.monthly_budget_minor],
            )
        )
        if status == 1:
            self._notify(f"{day_key}:100", "critical", f"來源 {source.id} 已達每日呼叫上限",
                         f"今日 {calls}/{source.daily_call_limit} 次，後續請求拒絕")
            raise BudgetExceeded(f"{source.id} 今日呼叫已達上限 {source.daily_call_limit}")
        if status == 2:
            self._notify(f"{month_key}:100", "critical", f"來源 {source.id} 已達每月預算",
                         f"本月 {spent}/{source.monthly_budget_minor} {source.budget_currency}（最小單位）")
            raise BudgetExceeded(f"{source.id} 本月預算已用盡")
        self._warn_if_near(day_key, calls, source.daily_call_limit, f"來源 {source.id} 每日呼叫",
                           "次")
        if source.monthly_budget_minor > 0:
            self._warn_if_near(month_key, spent, source.monthly_budget_minor, f"來源 {source.id} 每月預算",
                               f"{source.budget_currency}（最小單位）")

    def _warn_if_near(self, key: str, used: int, limit: int, title: str, unit: str) -> None:
        if used >= limit:
            self._notify(f"{key}:100", "critical", f"{title}已用完", f"{used}/{limit} {unit}")
        elif used >= ceil(limit * self._alert_percent / 100):
            self._notify(f"{key}:{self._alert_percent}", "warning", f"{title}已達 {self._alert_percent}%",
                         f"{used}/{limit} {unit}")
