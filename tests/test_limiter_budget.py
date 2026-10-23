from datetime import timedelta

import pytest

from app.compliance.budget import BudgetGuard
from app.compliance.errors import BudgetExceeded, RateLimited
from app.compliance.limiter import RateLimiter
from app.models import Source


def test_rate_limiter_enforces_interval_per_host(redis, clock):
    limiter = RateLimiter(redis, clock)
    limiter.acquire("a.example.com", 10)
    limiter.acquire("other.example.com", 10)  # 不同網域互不影響
    clock.advance(seconds=9.5)
    with pytest.raises(RateLimited) as exc:
        limiter.acquire("a.example.com", 10)
    assert exc.value.retry_after_seconds == pytest.approx(0.5)
    clock.advance(seconds=0.5)
    limiter.acquire("a.example.com", 10)


def test_rejected_attempt_does_not_reset_the_window(redis, clock):
    limiter = RateLimiter(redis, clock)
    limiter.acquire("a.example.com", 10)
    clock.advance(seconds=5)
    with pytest.raises(RateLimited):
        limiter.acquire("a.example.com", 10)
    clock.advance(seconds=5)
    limiter.acquire("a.example.com", 10)


def _source(**overrides) -> Source:
    fields = dict(id="s", daily_call_limit=10, cost_per_call_minor=0, monthly_budget_minor=0, budget_currency="USD")
    fields.update(overrides)
    return Source(**fields)


def test_budget_month_rolls_over(redis, clock):
    notes = []
    guard = BudgetGuard(redis, clock, 80, lambda *a: notes.append(a))
    source = _source(daily_call_limit=1000, cost_per_call_minor=10, monthly_budget_minor=20)
    guard.reserve(source)
    guard.reserve(source)
    with pytest.raises(BudgetExceeded):
        guard.reserve(source)
    clock.now = clock.now.replace(day=1) + timedelta(days=32)
    guard.reserve(source)


def test_budget_denial_does_not_consume(redis, clock):
    guard = BudgetGuard(redis, clock, 80, lambda *a: None)
    source = _source(daily_call_limit=1)
    guard.reserve(source)
    for _ in range(3):
        with pytest.raises(BudgetExceeded):
            guard.reserve(source)
    assert int(redis.get(f"budget:calls:s:{clock():%Y%m%d}")) == 1


def test_budget_warns_at_80_percent(redis, clock):
    notes = []
    guard = BudgetGuard(redis, clock, 80, lambda key, level, title, detail: notes.append((level, title)))
    source = _source(daily_call_limit=10)
    for _ in range(8):
        guard.reserve(source)
    assert notes[-1] == ("warning", "來源 s 每日呼叫已達 80%")
    assert len([n for n in notes if n[0] == "warning"]) >= 1


def test_interval_is_the_stricter_of_consecutive_callers(redis, clock):
    limiter = RateLimiter(redis, clock)
    limiter.acquire("shared.example.com", 1)
    clock.advance(seconds=1)
    # 要求 10 秒的呼叫者：從網域最後一次請求起算，不管上一次是誰發的
    with pytest.raises(RateLimited) as exc:
        limiter.acquire("shared.example.com", 10)
    assert exc.value.retry_after_seconds == pytest.approx(9)
    clock.advance(seconds=9)
    limiter.acquire("shared.example.com", 10)
    # 只要求 1 秒的呼叫者：仍要等滿上一位要求的 10 秒
    clock.advance(seconds=5)
    with pytest.raises(RateLimited) as exc:
        limiter.acquire("shared.example.com", 1)
    assert exc.value.retry_after_seconds == pytest.approx(5)
