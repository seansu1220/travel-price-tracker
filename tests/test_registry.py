from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.clock import utcnow
from app.compliance.registry import pause_source, register_source, resume_source, review_is_current
from app.models import Source
from tests.conftest import START, registration


@pytest.mark.parametrize(
    "overrides",
    [
        {"reviewer": "  "},
        {"terms_url": "not-a-url"},
        {"tier": "D"},
        {"kind": "train"},
        {"id": "Bad ID"},
        {"allowed_hosts": []},  # B 級必須有允許主機
        {"allowed_paths": []},
        {"allowed_paths": ["fares/"]},
        {"allowed_hosts": ["*.example.com"]},
        {"allowed_hosts": ["b.example.com/fares"]},
        {"tier": "C"},  # C 級不可設定主機與路徑
        {"cost_per_call_minor": 5},  # 有成本卻沒有預算
        {"daily_call_limit": 0},
        {"min_interval_seconds": 0},
        {"reviewed_at": utcnow().date() + timedelta(days=1)},
    ],
)
def test_registration_rejects_incomplete_or_invalid_data(overrides):
    with pytest.raises(ValidationError):
        registration(**overrides)


@pytest.mark.parametrize("missing", ["tier", "terms_url", "reviewed_at", "reviewer"])
def test_required_fields(missing):
    data = registration().model_dump()
    del data[missing]
    with pytest.raises(ValidationError):
        type(registration())(**data)


def test_b_tier_defaults_follow_spec():
    reg = registration()
    reg2 = type(reg)(**{k: v for k, v in reg.model_dump().items() if k not in ("min_interval_seconds", "daily_call_limit")})
    assert (reg2.min_interval_seconds, reg2.daily_call_limit) == (10, 500)


def test_c_tier_registration_without_hosts_is_valid():
    reg = registration(tier="C", allowed_hosts=[], allowed_paths=[])
    assert reg.tier == "C"


def test_hosts_are_normalized_to_lowercase():
    assert registration(allowed_hosts=["B.Example.COM"]).allowed_hosts == ["b.example.com"]


def test_review_age_boundary():
    source = Source(reviewed_at=date(2026, 1, 1))
    assert review_is_current(source, date(2026, 1, 1) + timedelta(days=180), 180)
    assert not review_is_current(source, date(2026, 1, 1) + timedelta(days=181), 180)


def test_reregistering_keeps_pause_and_resume_requires_reviewer(session_factory):
    with session_factory.begin() as session:
        register_source(session, registration())
        pause_source(session, "b-site", "http_429", START)
    with session_factory.begin() as session:
        register_source(session, registration(name="改名"))
    with session_factory.begin() as session:
        source = session.get(Source, "b-site")
        assert source.name == "改名"
        assert source.paused_reason == "http_429"
        with pytest.raises(ValueError):
            resume_source(session, "b-site", " ", date(2026, 10, 23))
        resume_source(session, "b-site", "Sean", date(2026, 10, 23))
    with session_factory() as session:
        source = session.get(Source, "b-site")
        assert source.paused_at is None and source.reviewed_at == date(2026, 10, 23)
