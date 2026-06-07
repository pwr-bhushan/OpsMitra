"""RED-phase tests for Step 10 WS-D (M6): detect_anomalies thresholds wiring.

These tests verify that:
1. detect_anomalies accepts a `thresholds` kwarg.
2. High threshold overrides suppress expected anomalies.
3. Lower threshold overrides produce more anomalies than defaults.
4. Runtime passes its DetectorThresholds through to detect_anomalies.

All tests will RED until Step 10 Phase A (M6) is implemented:
  - detect_anomalies gains a `thresholds` kwarg.
  - Runtime.execute passes self._thresholds into detect_anomalies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from opsmitra.config import DetectorThresholds
from opsmitra.detectors import DetectionConfig, detect_anomalies
from opsmitra.models import Anomaly, Event
from opsmitra.cooldown import AnomalyCooldown
from opsmitra.runtime import Runtime


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _utc(hour: int = 10, *, day_offset: int = 0) -> datetime:
    base = datetime(2026, 6, 7, tzinfo=timezone.utc)
    return base + timedelta(hours=hour, days=day_offset)


WINDOW_START = _utc(10)
WINDOW_END = _utc(11)


def _make_event(
    *,
    ts: datetime,
    tenant_id: str = "tenant_acme",
    endpoint: str = "/auth/login",
    status_code: int = 401,
    ip: str = "10.0.0.1",
    user_id: str = "user_1",
    cost_units: float = 0.0,
) -> Event:
    import uuid
    return Event(
        timestamp=ts,
        tenant_id=tenant_id,
        endpoint=endpoint,
        method="POST",
        status_code=status_code,
        cost_units=cost_units,
        request_id=str(uuid.uuid4()),
        user_id=user_id,
        ip=ip,
    )


def _burst_events(count: int = 15, *, distinct_users: int = 5) -> list[Event]:
    """Generate `count` auth failure events from a single IP across `distinct_users`."""
    events = []
    for i in range(count):
        ts = WINDOW_START + timedelta(seconds=i * 2)
        user = f"user_{i % distinct_users}"
        events.append(_make_event(ts=ts, user_id=user))
    return events


def _sms_events(count: int = 10) -> list[Event]:
    """Generate `count` SMS send events for a single tenant/API key."""
    events = []
    for i in range(count):
        ts = WINDOW_START + timedelta(seconds=i * 5)
        events.append(
            _make_event(
                ts=ts,
                endpoint="/sms/send",
                status_code=200,
                ip=None,
            )
        )
    return events


# ---------------------------------------------------------------------------
# Fake collaborators for Runtime tests
# ---------------------------------------------------------------------------


class _FakeSource:
    def __init__(self, events: list[Event]) -> None:
        self._events = events

    def fetch_events(self, window_start, window_end, tenant=None, types=None):
        return iter(self._events)


class _FakeSummarizer:
    def summarize(self, anomaly: Anomaly):
        from opsmitra.summarizer import AlertSummary
        return AlertSummary(
            title="test",
            severity="high",
            summary="test",
            likely_cause="test",
            recommended_action="test",
            confidence="low",
        )


class _FakeAlerter:
    def send(self, summary, anomaly):
        from dataclasses import dataclass

        @dataclass
        class _FakeResult:
            delivered: bool = True
            dry_run: bool = True
            attempts: int = 1

        return _FakeResult()


# ---------------------------------------------------------------------------
# Test 1 — Impossibly high threshold suppresses sms_abuse anomalies
# ---------------------------------------------------------------------------


def test_detect_anomalies_uses_threshold_overrides():
    """detect_anomalies with an impossibly high sms_abuse_rate_per_minute must return
    NO sms_abuse anomalies even when the events would trip the default threshold.

    RED: detect_anomalies currently has no `thresholds` parameter.
    """
    # 600 SMS events in 1 hour = 10/min, normally would trigger abuse spike
    sms = _sms_events(count=600)
    thresholds = DetectorThresholds(sms_abuse_rate_per_minute=999_999)

    # This call will TypeError until `thresholds` kwarg is added (RED)
    anomalies = detect_anomalies(
        sms,
        WINDOW_START,
        WINDOW_END,
        config=DetectionConfig(),
        thresholds=thresholds,
    )

    sms_anomalies = [a for a in anomalies if a.type == "sms_abuse_spike"]
    assert sms_anomalies == [], (
        f"Expected no sms_abuse anomalies with impossibly high threshold, "
        f"got {len(sms_anomalies)}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Lower auth_burst threshold triggers more anomalies
# ---------------------------------------------------------------------------


def test_detect_anomalies_threshold_override_applied_per_field():
    """Providing a lower auth_burst_count threshold must produce more anomalies
    than the default (default auth_min_failures=100; override to 5).

    RED: detect_anomalies currently has no `thresholds` parameter.
    """
    # 15 auth failures from one IP across 5 distinct users
    events = _burst_events(count=15, distinct_users=5)

    # Default threshold: auth_min_failures=100 → should NOT detect (15 < 100)
    default_anomalies = detect_anomalies(events, WINDOW_START, WINDOW_END)
    default_auth = [a for a in default_anomalies if a.type == "auth_failure_burst"]

    # Low threshold: auth_burst_count=3, auth_min_users=1 (via thresholds kwarg)
    low_thresholds = DetectorThresholds(auth_burst_count=3)
    # RED: thresholds kwarg doesn't exist yet
    low_anomalies = detect_anomalies(
        events,
        WINDOW_START,
        WINDOW_END,
        thresholds=low_thresholds,
    )
    low_auth = [a for a in low_anomalies if a.type == "auth_failure_burst"]

    assert len(low_auth) > len(default_auth), (
        f"Lower threshold must produce more auth_failure_burst anomalies: "
        f"default={len(default_auth)}, low_threshold={len(low_auth)}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Runtime passes DetectorThresholds to detect_anomalies
# ---------------------------------------------------------------------------


def test_runtime_passes_thresholds_to_detect_anomalies(tmp_path, monkeypatch):
    """Runtime.execute must call detect_anomalies with thresholds=<the custom instance>.

    RED: runtime.py currently calls detect_anomalies without a thresholds kwarg.
    """
    custom_thresholds = DetectorThresholds(auth_burst_count=42)
    source = _FakeSource([])
    cooldown = AnomalyCooldown(tmp_path / "cooldown.json", window_seconds=3600)

    runtime = Runtime(
        source=source,
        summarizer=_FakeSummarizer(),
        alerter=_FakeAlerter(),
        cooldown=cooldown,
        thresholds=custom_thresholds,
        dry_run=True,
    )

    captured_kwargs: dict = {}

    original_detect = detect_anomalies

    def spy_detect(events, window_start, window_end, **kwargs):
        captured_kwargs.update(kwargs)
        return original_detect(events, window_start, window_end, **kwargs)

    monkeypatch.setattr(
        "opsmitra.runtime.detect_anomalies",
        spy_detect,
    )

    runtime.execute(WINDOW_START, WINDOW_END)

    # RED: until M6 is wired, captured_kwargs will have no 'thresholds' key
    assert "thresholds" in captured_kwargs, (
        "detect_anomalies was not called with a 'thresholds' keyword argument by Runtime"
    )
    assert captured_kwargs["thresholds"] is custom_thresholds, (
        f"Expected thresholds={custom_thresholds!r}, "
        f"got {captured_kwargs.get('thresholds')!r}"
    )
