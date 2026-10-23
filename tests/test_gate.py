"""P0 驗收關卡：未登錄或禁止的來源一律被拒；被拒時不發出任何請求並留下稽核紀錄。"""

from datetime import date

import httpx
import pytest
from sqlalchemy import select

from app.compliance import errors
from app.compliance.gate import ComplianceGate
from app.config import Settings
from app.models import FetchLog, Source
from tests.conftest import fixture_text

FARES_URL = "https://b.example.com/fares/tpe-nrt"
ROBOTS_URL = "https://b.example.com/robots.txt"


def logs(session_factory) -> list[FetchLog]:
    with session_factory() as session:
        return list(session.scalars(select(FetchLog).order_by(FetchLog.id)))


def mock_b_site(http, robots_body=None, robots_status=200, page_status=200, page_body=None):
    robots = http.get(ROBOTS_URL).mock(
        return_value=httpx.Response(robots_status, text=robots_body or fixture_text("robots/b_site.txt"))
    )
    page = http.get(FARES_URL).mock(
        return_value=httpx.Response(
            page_status,
            text=page_body or fixture_text("pages/fares.html"),
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    return robots, page


# ---- 拒絕情境：未登錄、C 級、過期、停用、暫停 ----


def test_unregistered_source_is_denied_without_any_request(gate, http, session_factory):
    route = http.get(FARES_URL).mock(return_value=httpx.Response(200))
    with pytest.raises(errors.SourceNotRegistered):
        gate.request("unknown", "GET", FARES_URL)
    assert not route.called
    [log] = logs(session_factory)
    assert (log.source_id, log.decision, log.status_code) == ("unknown", "denied:source_not_registered", None)


def test_tier_c_source_is_always_denied(gate, http, add_source, session_factory):
    add_source(id="c-site", tier="C", allowed_hosts=[], allowed_paths=[])
    route = http.route().mock(return_value=httpx.Response(200))
    with pytest.raises(errors.SourceTierC):
        gate.request("c-site", "GET", "https://c.example.com/")
    assert not route.called
    assert logs(session_factory)[0].decision == "denied:source_tier_c"


def test_tier_c_cannot_be_smuggled_in_by_editing_the_table(gate, http, add_source, session_factory):
    add_source(id="c-site", tier="C", allowed_hosts=[], allowed_paths=[])
    with session_factory.begin() as session:
        source = session.get(Source, "c-site")
        source.allowed_hosts = ["c.example.com"]
        source.allowed_paths = ["/"]
    with pytest.raises(errors.SourceTierC):
        gate.request("c-site", "GET", "https://c.example.com/")


def test_review_older_than_180_days_is_denied(gate, http, add_source, clock, alerter, session_factory):
    add_source(reviewed_at=date(2026, 10, 1))
    mock_b_site(http)
    clock.advance(days=158)  # 2027-03-30：距審閱 180 天，仍有效
    gate.request("b-site", "GET", FARES_URL)
    clock.advance(days=1)  # 181 天：過期
    with pytest.raises(errors.SourceReviewExpired):
        gate.request("b-site", "GET", FARES_URL)
    assert "來源 b-site 審閱已過期" in alerter.titles()
    assert logs(session_factory)[-1].decision == "denied:source_review_expired"


def test_disabled_source_is_denied(gate, http, add_source):
    add_source()
    with session_factory_begin(gate) as session:
        session.get(Source, "b-site").enabled = False
    route = http.route().mock(return_value=httpx.Response(200))
    with pytest.raises(errors.SourceDisabled):
        gate.request("b-site", "GET", FARES_URL)
    assert not route.called


def test_paused_source_is_denied(gate, http, add_source, clock):
    add_source()
    with session_factory_begin(gate) as session:
        source = session.get(Source, "b-site")
        source.paused_at = clock()
        source.paused_reason = "人工暫停"
    route = http.route().mock(return_value=httpx.Response(200))
    with pytest.raises(errors.SourcePaused):
        gate.request("b-site", "GET", FARES_URL)
    assert not route.called


def session_factory_begin(gate: ComplianceGate):
    return gate._session_factory.begin()


# ---- 網址範圍 ----


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/fares/x",  # 不在允許主機
        "https://b.example.com/account/me",  # 不在允許路徑
        "https://b.example.com/fares/../account/me",  # 路徑穿越
        "https://b.example.com/fares/%2e%2e/account",  # 編碼後的路徑穿越
        "https://user:pw@b.example.com/fares/x",  # 帶帳密（不登入）
        "ftp://b.example.com/fares/x",  # 非 http(s)
        "https://B.EXAMPLE.COM.evil.net/fares/x",  # 假冒子網域
    ],
)
def test_urls_outside_registered_scope_are_denied(gate, http, add_source, url):
    add_source()
    route = http.route().mock(return_value=httpx.Response(200))
    with pytest.raises(errors.UrlNotAllowed):
        gate.request("b-site", "GET", url)
    assert not route.called


# ---- robots.txt ----


def test_robots_allowed_request_goes_out_with_honest_user_agent(gate, http, add_source, session_factory):
    add_source()
    robots, page = mock_b_site(http)
    response = gate.request("b-site", "GET", FARES_URL, headers={"User-Agent": "Mozilla/5.0 Chrome"})
    assert response.status_code == 200
    sent_ua = page.calls.last.request.headers["user-agent"]
    assert sent_ua == "TravelPriceTrackerBot/0.1.0 (+mailto:ops@example.com)"
    assert "Mozilla" not in sent_ua
    decisions = [(log.url, log.decision, log.robots_verdict, log.status_code) for log in logs(session_factory)]
    assert decisions == [
        (ROBOTS_URL, "robots_fetch", "not_applicable", 200),
        (FARES_URL, "allowed", "allowed", 200),
    ]


def test_robots_disallowed_path_is_denied_and_alerted(gate, http, add_source, alerter, session_factory):
    add_source(allowed_paths=["/fares/", "/search/"])
    mock_b_site(http)
    page = http.get("https://b.example.com/search/private").mock(return_value=httpx.Response(200))
    with pytest.raises(errors.RobotsDisallowed):
        gate.request("b-site", "GET", "https://b.example.com/search/private")
    assert not page.called
    assert "來源 b-site 的 robots.txt 不允許" in alerter.titles()
    log = logs(session_factory)[-1]
    assert (log.decision, log.robots_verdict) == ("denied:robots_disallowed", "disallowed")


def test_robots_rule_targeting_our_bot_is_respected(gate, http, add_source):
    add_source()
    _, page = mock_b_site(http, robots_body=fixture_text("robots/deny_our_bot.txt"))
    with pytest.raises(errors.RobotsDisallowed):
        gate.request("b-site", "GET", FARES_URL)
    assert not page.called


@pytest.mark.parametrize(
    ("status", "allowed"),
    [(404, True), (410, True), (401, False), (403, False), (500, False), (503, False), (301, False)],
)
def test_robots_fetch_status_rules(gate, http, add_source, status, allowed):
    add_source()
    _, page = mock_b_site(http, robots_status=status, robots_body="")
    if allowed:
        gate.request("b-site", "GET", FARES_URL)
        assert page.called
    else:
        with pytest.raises(errors.RobotsDisallowed):
            gate.request("b-site", "GET", FARES_URL)
        assert not page.called


def test_robots_network_error_is_treated_as_disallowed(gate, http, add_source, session_factory):
    add_source()
    http.get(ROBOTS_URL).mock(side_effect=httpx.ConnectError("down"))
    page = http.get(FARES_URL).mock(return_value=httpx.Response(200))
    with pytest.raises(errors.RobotsDisallowed):
        gate.request("b-site", "GET", FARES_URL)
    assert not page.called
    robots_log, denied_log = logs(session_factory)
    assert (robots_log.decision, robots_log.status_code) == ("robots_fetch", None)
    assert (denied_log.decision, denied_log.robots_verdict) == ("denied:robots_disallowed", "unavailable")


def test_robots_is_cached_for_24_hours(gate, http, add_source, clock, redis):
    add_source()
    robots, page = mock_b_site(http)
    gate.request("b-site", "GET", FARES_URL)
    clock.advance(seconds=10)
    gate.request("b-site", "GET", FARES_URL)
    assert robots.call_count == 1
    assert page.call_count == 2
    assert 86000 < redis.ttl(f"robots:{ROBOTS_URL}") <= 86400


def test_tier_a_does_not_fetch_robots(gate, http, add_source, session_factory):
    add_source(id="api-a", tier="A", allowed_hosts=["api.example.com"], allowed_paths=["/air/"])
    robots = http.get("https://api.example.com/robots.txt").mock(return_value=httpx.Response(200))
    api = http.post("https://api.example.com/air/offer_requests").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    gate.request("api-a", "POST", "https://api.example.com/air/offer_requests", json={"q": 1})
    assert api.called and not robots.called
    assert logs(session_factory)[-1].robots_verdict == "not_applicable"


# ---- 封鎖：403、429、驗證碼頁 → 暫停、告警、不重試 ----


@pytest.mark.parametrize(
    ("status", "body", "content_type", "reason"),
    [
        (403, "Forbidden", "text/plain", "http_403"),
        (429, "Too Many Requests", "text/plain", "http_429"),
        (200, fixture_text("pages/captcha.html"), "text/html", "block_page:captcha"),
    ],
)
def test_block_response_pauses_source_and_never_retries(
    gate, http, add_source, alerter, clock, session_factory, status, body, content_type, reason
):
    add_source()
    http.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text=fixture_text("robots/b_site.txt")))
    page = http.get(FARES_URL).mock(
        return_value=httpx.Response(status, text=body, headers={"content-type": content_type})
    )
    with pytest.raises(errors.SourceBlocked):
        gate.request("b-site", "GET", FARES_URL)
    assert page.call_count == 1

    with session_factory() as session:
        source = session.get(Source, "b-site")
        assert source.paused_at is not None
        assert source.paused_reason.startswith(reason)
    assert alerter.alerts[-1][0] == "critical"
    assert "來源 b-site 已暫停" in alerter.titles()
    assert logs(session_factory)[-1].decision == f"blocked:{reason}"

    clock.advance(hours=1)
    with pytest.raises(errors.SourcePaused):
        gate.request("b-site", "GET", FARES_URL)
    assert page.call_count == 1


def test_json_mentioning_captcha_is_not_a_block(gate, http, add_source):
    add_source(id="api-a", tier="A", allowed_hosts=["api.example.com"], allowed_paths=["/"])
    http.get("https://api.example.com/info").mock(
        return_value=httpx.Response(200, json={"note": "no captcha here"})
    )
    assert gate.request("api-a", "GET", "https://api.example.com/info").status_code == 200


def test_redirects_are_not_followed(gate, http, add_source):
    add_source()
    mock_b_site(http, page_status=302)
    http.get(FARES_URL).mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.example.com/"})
    )
    evil = http.get("https://evil.example.com/").mock(return_value=httpx.Response(200))
    assert gate.request("b-site", "GET", FARES_URL).status_code == 302
    assert not evil.called


def test_network_error_is_logged_and_not_retried(gate, http, add_source, session_factory):
    add_source()
    http.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    page = http.get(FARES_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(errors.FetchFailed, match="b-site"):
        gate.request("b-site", "GET", FARES_URL)
    assert page.call_count == 1
    assert logs(session_factory)[-1].decision == "error:network"


# ---- 限速與預算（經過閘門） ----


def test_rate_limit_applies_per_domain_across_sources(gate, http, add_source, clock):
    add_source()
    add_source(id="b-site-2", allowed_paths=["/fares/"])
    _, page = mock_b_site(http)
    gate.request("b-site", "GET", FARES_URL)
    with pytest.raises(errors.RateLimited) as exc:
        gate.request("b-site-2", "GET", FARES_URL)
    assert exc.value.retry_after_seconds == pytest.approx(10)
    clock.advance(seconds=10)
    gate.request("b-site-2", "GET", FARES_URL)
    assert page.call_count == 2


def test_daily_call_limit_blocks_and_alerts(gate, http, add_source, clock, alerter):
    add_source(daily_call_limit=5, min_interval_seconds=1)
    _, page = mock_b_site(http)
    for _ in range(5):
        gate.request("b-site", "GET", FARES_URL)
        clock.advance(seconds=1)
    with pytest.raises(errors.BudgetExceeded):
        gate.request("b-site", "GET", FARES_URL)
    assert page.call_count == 5
    titles = alerter.titles()
    assert titles.count("來源 b-site 每日呼叫已達 80%") == 1
    assert titles.count("來源 b-site 每日呼叫已用完") == 1
    clock.advance(days=1)
    gate.request("b-site", "GET", FARES_URL)
    assert page.call_count == 6


def test_monthly_budget_blocks_paid_source(gate, http, add_source, clock, alerter):
    add_source(
        id="api-a", tier="A", allowed_hosts=["api.example.com"], allowed_paths=["/"],
        min_interval_seconds=1, cost_per_call_minor=100, monthly_budget_minor=300,
    )
    api = http.get("https://api.example.com/q").mock(return_value=httpx.Response(200, json={}))
    for _ in range(3):
        gate.request("api-a", "GET", "https://api.example.com/q")
        clock.advance(seconds=1)
    with pytest.raises(errors.BudgetExceeded):
        gate.request("api-a", "GET", "https://api.example.com/q")
    assert api.call_count == 3
    assert "來源 api-a 每月預算已達 80%" not in alerter.titles()  # 200/300 未達 80%，300/300 直接 100%
    assert "來源 api-a 每月預算已用完" in alerter.titles()


# ---- 設定 ----


def test_gate_refuses_to_start_without_contact_email(session_factory, redis):
    with pytest.raises(errors.GateMisconfigured):
        ComplianceGate(
            session_factory=session_factory, redis=redis,
            settings=Settings(_env_file=None, crawler_contact_email=""),
        )


def test_browser_flow_authorize_then_report(gate, http, add_source, session_factory):
    """P3 瀏覽器配接器的用法：先取得許可，自行載入頁面後回報結果。"""
    add_source()
    http.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text=fixture_text("robots/b_site.txt")))
    permit = gate.authorize("b-site", FARES_URL)
    assert permit.robots_verdict == "allowed"
    with pytest.raises(errors.SourceBlocked):
        gate.report(permit, 200, content_type="text/html", body_text=fixture_text("pages/captcha.html"))
    with pytest.raises(errors.SourcePaused):
        gate.authorize("b-site", FARES_URL)


# ---- robots.txt 請求：寫入稽核日誌，但不計入限速與預算（擁有者 2026-10-23 決定） ----


def test_robots_fetch_is_audited_but_not_counted(gate, http, add_source, clock, redis, session_factory):
    add_source(daily_call_limit=1, min_interval_seconds=10)
    robots, page = mock_b_site(http)
    # 每日上限 1 次、間隔 10 秒：若 robots 請求有被計入，主請求會被擋下
    gate.request("b-site", "GET", FARES_URL)
    assert robots.call_count == 1 and page.call_count == 1
    assert int(redis.get(f"budget:calls:b-site:{clock():%Y%m%d}")) == 1

    robots_log, page_log = logs(session_factory)
    assert (robots_log.source_id, robots_log.url, robots_log.method) == ("b-site", ROBOTS_URL, "GET")
    assert (robots_log.decision, robots_log.status_code, robots_log.cost_minor) == ("robots_fetch", 200, 0)
    assert robots_log.duration_ms is not None
    assert page_log.decision == "allowed"


@pytest.mark.parametrize("status", [404, 403, 500])
def test_robots_fetch_is_audited_for_every_status(gate, http, add_source, session_factory, status):
    add_source()
    mock_b_site(http, robots_status=status, robots_body="")
    try:
        gate.request("b-site", "GET", FARES_URL)
    except errors.RobotsDisallowed:
        pass
    robots_log = logs(session_factory)[0]
    assert (robots_log.url, robots_log.decision, robots_log.status_code) == (ROBOTS_URL, "robots_fetch", status)


def test_cached_robots_is_not_fetched_or_logged_again(gate, http, add_source, clock, session_factory):
    add_source()
    mock_b_site(http)
    gate.request("b-site", "GET", FARES_URL)
    clock.advance(seconds=10)
    gate.request("b-site", "GET", FARES_URL)
    assert [log.decision for log in logs(session_factory)] == ["robots_fetch", "allowed", "allowed"]


# ---- 依賴服務故障：一律拒絕放行，錯誤訊息標明步驟 ----


def test_redis_outage_denies_with_location(session_factory, settings, alerter, clock, http, add_source):
    import fakeredis

    add_source()
    server = fakeredis.FakeServer()
    server.connected = False
    gate = ComplianceGate(
        session_factory=session_factory, redis=fakeredis.FakeRedis(server=server),
        settings=settings, alerter=alerter, clock=clock,
    )
    route = http.route().mock(return_value=httpx.Response(200))
    with pytest.raises(errors.GateUnavailable, match="檢查 robots.txt"):
        gate.request("b-site", "GET", FARES_URL)
    assert not route.called
    assert logs(session_factory)[-1].decision == "denied:gate_unavailable"
    gate.close()


def test_pause_failure_still_alerts_and_stops(gate, http, add_source, alerter, monkeypatch):
    from sqlalchemy.exc import OperationalError

    add_source()
    mock_b_site(http, page_status=429, page_body="slow down")

    def broken_pause(*args, **kwargs):
        raise OperationalError("UPDATE sources", {}, Exception("db down"))

    monkeypatch.setattr("app.compliance.gate.pause_source", broken_pause)
    with pytest.raises(errors.SourceBlocked):
        gate.request("b-site", "GET", FARES_URL)
    level, title, detail = alerter.alerts[-1]
    assert (level, title) == ("critical", "來源 b-site 被封鎖但暫停失敗")
    assert "請立即人工停用" in detail


def test_audit_write_failure_is_reported_with_context(gate, http, session_factory):
    from app.models import FetchLog as FetchLogModel

    FetchLogModel.__table__.drop(session_factory.kw["bind"])
    try:
        with pytest.raises(errors.AuditWriteError, match="fetch_logs.*unknown"):
            gate.request("unknown", "GET", FARES_URL)
    finally:
        FetchLogModel.__table__.create(session_factory.kw["bind"])


# ---- robots.txt 轉址（RFC 9309）：最多 5 次，只限同網域或其子網域（擁有者 2026-10-23 決定） ----

APEX_FARES = "https://example.com/fares/tpe-nrt"
ROBOTS_BODY = fixture_text("robots/b_site.txt")


def _redirect(location: str) -> httpx.Response:
    return httpx.Response(301, headers={"location": location})


def _apex_source(add_source):
    add_source(id="apex", allowed_hosts=["example.com"], allowed_paths=["/fares/"])


def _apex_page(http):
    return http.get(APEX_FARES).mock(
        return_value=httpx.Response(200, text="<p>TWD 4,560</p>", headers={"content-type": "text/html"})
    )


def test_robots_redirect_http_to_https_is_followed(gate, http, add_source, session_factory):
    _apex_source(add_source)
    http.get("http://example.com/robots.txt").mock(return_value=_redirect("https://example.com/robots.txt"))
    http.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_BODY))
    page = http.get("http://example.com/fares/tpe-nrt").mock(
        return_value=httpx.Response(200, text="ok", headers={"content-type": "text/plain"})
    )
    gate.request("apex", "GET", "http://example.com/fares/tpe-nrt")
    assert page.called
    robots_logs = [(log.url, log.status_code) for log in logs(session_factory) if log.decision == "robots_fetch"]
    assert robots_logs == [("http://example.com/robots.txt", 301), ("https://example.com/robots.txt", 200)]


def test_robots_redirect_to_subdomain_is_followed(gate, http, add_source):
    _apex_source(add_source)
    http.get("https://example.com/robots.txt").mock(return_value=_redirect("https://www.example.com/robots.txt"))
    http.get("https://www.example.com/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_BODY))
    page = _apex_page(http)
    gate.request("apex", "GET", APEX_FARES)
    assert page.called


def test_robots_redirect_rules_from_target_are_applied(gate, http, add_source):
    _apex_source(add_source)
    http.get("https://example.com/robots.txt").mock(return_value=_redirect("/robots/main.txt"))  # 相對路徑
    http.get("https://example.com/robots/main.txt").mock(
        return_value=httpx.Response(200, text=fixture_text("robots/deny_our_bot.txt"))
    )
    page = _apex_page(http)
    with pytest.raises(errors.RobotsDisallowed):
        gate.request("apex", "GET", APEX_FARES)
    assert not page.called


@pytest.mark.parametrize(
    "location",
    [
        "https://other.net/robots.txt",  # 其他網域
        "https://example.com.evil.net/robots.txt",  # 假冒網域
        "https://notexample.com/robots.txt",  # 字尾相同但不是子網域
        "ftp://example.com/robots.txt",  # 非 http(s)
    ],
)
def test_robots_redirect_to_other_domain_is_denied(gate, http, add_source, session_factory, location):
    _apex_source(add_source)
    http.get("https://example.com/robots.txt").mock(return_value=_redirect(location))
    elsewhere = http.route(url__regex=r"^(?!https://example\.com/robots\.txt).*robots\.txt$").mock(
        return_value=httpx.Response(200, text="")
    )
    page = _apex_page(http)
    with pytest.raises(errors.RobotsDisallowed) as exc:
        gate.request("apex", "GET", APEX_FARES)
    assert exc.value.verdict == "unavailable"
    assert not elsewhere.called and not page.called
    denied = [log for log in logs(session_factory) if log.decision == "robots_redirect_denied"]
    assert len(denied) == 1 and "其他網域" in denied[0].detail


def test_robots_redirect_to_parent_domain_is_denied(gate, http, add_source):
    add_source(id="www", allowed_hosts=["www.example.com"], allowed_paths=["/fares/"])
    http.get("https://www.example.com/robots.txt").mock(return_value=_redirect("https://example.com/robots.txt"))
    apex_robots = http.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=""))
    with pytest.raises(errors.RobotsDisallowed):
        gate.request("www", "GET", "https://www.example.com/fares/x")
    assert not apex_robots.called


def _redirect_chain(http, hops: int):
    """建立 hops 次轉址後回 200 的 robots.txt 鏈。"""
    for index in range(hops):
        current = "https://example.com/robots.txt" if index == 0 else f"https://example.com/r{index}.txt"
        http.get(current).mock(return_value=_redirect(f"/r{index + 1}.txt"))
    final = http.get(f"https://example.com/r{hops}.txt").mock(return_value=httpx.Response(200, text=ROBOTS_BODY))
    return final


def test_robots_follows_up_to_five_redirects(gate, http, add_source):
    _apex_source(add_source)
    final = _redirect_chain(http, 5)
    page = _apex_page(http)
    gate.request("apex", "GET", APEX_FARES)
    assert final.called and page.called


def test_robots_more_than_five_redirects_is_denied(gate, http, add_source, session_factory):
    _apex_source(add_source)
    final = _redirect_chain(http, 6)
    page = _apex_page(http)
    with pytest.raises(errors.RobotsDisallowed):
        gate.request("apex", "GET", APEX_FARES)
    assert not final.called and not page.called
    entries = logs(session_factory)
    assert [log.decision for log in entries].count("robots_fetch") == 6
    assert "超過 5 次" in entries[-2].detail  # 最後一筆是閘門拒絕主請求的紀錄


def test_robots_redirect_without_location_is_denied(gate, http, add_source):
    _apex_source(add_source)
    http.get("https://example.com/robots.txt").mock(return_value=httpx.Response(302))
    page = _apex_page(http)
    with pytest.raises(errors.RobotsDisallowed):
        gate.request("apex", "GET", APEX_FARES)
    assert not page.called


# ---- 限速以網域全域計算（擁有者 2026-10-23 確認） ----


def test_sources_sharing_a_domain_obey_the_stricter_interval(gate, http, add_source, clock):
    add_source(id="strict", min_interval_seconds=10)
    add_source(id="lenient", min_interval_seconds=1)
    _, page = mock_b_site(http)
    gate.request("strict", "GET", FARES_URL)
    clock.advance(seconds=1)
    with pytest.raises(errors.RateLimited) as exc:
        gate.request("lenient", "GET", FARES_URL)  # 自己只要求 1 秒，但上一次是 10 秒的來源
    assert exc.value.retry_after_seconds == pytest.approx(9)
    clock.advance(seconds=9)
    gate.request("lenient", "GET", FARES_URL)
    clock.advance(seconds=1)
    with pytest.raises(errors.RateLimited):
        gate.request("strict", "GET", FARES_URL)  # 嚴格的來源仍要等滿自己的 10 秒
    assert page.call_count == 2
