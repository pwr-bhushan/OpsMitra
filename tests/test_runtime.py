"""RED-phase tests for opsmitra.runtime.

All imports from opsmitra.runtime are expected to fail at collection until
src/opsmitra/runtime.py is implemented (Step 8, Phase C).

Design decisions:
- Fake collaborators (FakeEventSource, FakeSummarizer, FakeAlerter) avoid
  all network/LLM I/O.
- Clock injection allows deterministic time control without freezegun.
- tmp_path fixture used for all filesystem paths.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence
from unittest.mock import patch

import pytest

from opsmitra.config import DetectorThresholds
from opsmitra.models import Anomaly, Event
from opsmitra.summarizer import AlertSummary, FallbackSummarizer
from opsmitra.slack_alerter import DeliveryResult

# These imports are expected to fail (ImportError) until the module exists.
from opsmitra.runtime import (  # noqa: E402
    EventSourceError,
    Runtime,
    RuntimeResult,
    SummarizerError,
)
from opsmitra.cooldown import AnomalyCooldown


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_WINDOW_START = datetime(2026, 6, 7, 11, 0, 0, tzinfo=timezone.utc)
_WINDOW_END = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)
_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)

_WEBHOOK_SENTINEL = "https://hooks.slack.com/services/SENTINEL-RUNTIME-TESTS"


# ---------------------------------------------------------------------------
# Fake collaborators
# ---------------------------------------------------------------------------


class FakeEventSource:
    """Returns a fixed list of events; optionally raises on fetch."""

    def __init__(
        self,
        events: list[Event] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._events = events or []
        self._raise_exc = raise_exc

    def fetch_events(
        self,
        window_start: datetime,
        window_end: datetime,
        tenant: str | None = None,
        types: Sequence[str] | None = None,
    ) -> Iterable[Event]:
        if self._raise_exc is not None:
            raise self._raise_exc
        return iter(self._events)


class FakeSummarizer:
    """Returns a fixed AlertSummary; optionally raises on summarize."""

    def __init__(self, raise_exc: Exception | None = None) -> None:
        self._raise_exc = raise_exc

    def summarize(self, anomaly: Anomaly) -> AlertSummary:
        if self._raise_exc is not None:
            raise self._raise_exc
        return AlertSummary(
            title="Fake alert",
            severity=anomaly.severity,
            summary="Fake summary.",
            likely_cause="Fake cause.",
            recommended_action="Fake action.",
            confidence="low",
        )


class FakeAlerter:
    """Records calls; optionally raises or returns delivered=False."""

    def __init__(
        self,
        *,
        delivered: bool = True,
        raise_exc: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[AlertSummary, Anomaly]] = []
        self._delivered = delivered
        self._raise_exc = raise_exc

    def send(self, summary: AlertSummary, anomaly: Anomaly) -> DeliveryResult:
        self.calls.append((summary, anomaly))
        if self._raise_exc is not None:
            raise self._raise_exc
        return DeliveryResult(
            delivered=self._delivered,
            dry_run=True,
            attempts=1,
            status_code=None,
            payload={},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sms_anomaly(tenant: str = "tenant_acme") -> Anomaly:
    return Anomaly(
        id="anom_test_1",
        type="sms_abuse_spike",
        severity="high",
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
        tenant_id=tenant,
        subject={"endpoint": "/sms/send", "api_key_id": "key_abc"},
        observed={"count": 600},
        baseline={"hourly_count": 40},
        ratio=15.0,
        evidence={"notes": ["unusual SMS volume"], "sample_request_ids": ["req_001"]},
        recommended_actions=["Throttle the API key"],
    )


def _make_runtime(
    tmp_path: Path,
    *,
    source: FakeEventSource | None = None,
    summarizer: FakeSummarizer | None = None,
    alerter: FakeAlerter | None = None,
    clock: Callable[[], datetime] | None = None,
    max_events_per_window: int = 1_000_000,
    cooldown_window_seconds: int = 3600,
) -> Runtime:
    return Runtime(
        source=source or FakeEventSource(),
        summarizer=summarizer or FakeSummarizer(),
        alerter=alerter or FakeAlerter(),
        cooldown=AnomalyCooldown(
            tmp_path / "cooldown.json",
            window_seconds=cooldown_window_seconds,
            clock=clock or (lambda: _NOW),
        ),
        thresholds=DetectorThresholds(),
        clock=clock or (lambda: _NOW),
        max_events_per_window=max_events_per_window,
    )


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_runtime_happy_path_no_anomalies(tmp_path: Path):
    """When no events match anomaly thresholds, result has 0 detected/alerted/suppressed."""
    runtime = _make_runtime(tmp_path, source=FakeEventSource(events=[]))
    result = runtime.execute(_WINDOW_START, _WINDOW_END)

    assert isinstance(result, RuntimeResult)
    assert result.anomalies_detected == 0
    assert result.anomalies_alerted == 0
    assert result.anomalies_suppressed == 0
    assert result.errors == ()


def test_runtime_detects_anomaly_and_sends_dry_run(tmp_path: Path):
    """When detect_anomalies returns anomalies, alerter is called once and result reflects dry_run."""
    alerter = FakeAlerter(delivered=True)

    with patch("opsmitra.runtime.detect_anomalies", return_value=[_make_sms_anomaly()]):
        runtime = _make_runtime(tmp_path, alerter=alerter)
        result = runtime.execute(_WINDOW_START, _WINDOW_END)

    assert len(alerter.calls) == 1
    assert result.dry_run is True
    assert result.anomalies_alerted == 1


def test_runtime_suppresses_repeat_anomaly_via_cooldown(tmp_path: Path):
    """The same fingerprint in two consecutive execute() calls must be suppressed on the second."""
    cooldown = AnomalyCooldown(
        tmp_path / "cooldown.json",
        window_seconds=3600,
        clock=lambda: _NOW,
    )
    alerter = FakeAlerter(delivered=True)
    runtime = Runtime(
        source=FakeEventSource(),
        summarizer=FakeSummarizer(),
        alerter=alerter,
        cooldown=cooldown,
        thresholds=DetectorThresholds(),
        clock=lambda: _NOW,
    )

    anomaly = _make_sms_anomaly()

    with patch("opsmitra.runtime.detect_anomalies", return_value=[anomaly]):
        result1 = runtime.execute(_WINDOW_START, _WINDOW_END)

    with patch("opsmitra.runtime.detect_anomalies", return_value=[anomaly]):
        result2 = runtime.execute(_WINDOW_START, _WINDOW_END)

    assert result1.anomalies_alerted == 1
    assert result2.anomalies_alerted == 0
    assert result2.anomalies_suppressed == 1


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


def test_runtime_records_event_source_error_in_result(tmp_path: Path):
    """When EventSource.fetch_events raises, runtime wraps it as EventSourceError
    (re-raises per plan §6 Stage 2 — source errors are terminal)."""
    source = FakeEventSource(raise_exc=RuntimeError("fetch failed"))
    runtime = _make_runtime(tmp_path, source=source)

    with pytest.raises(EventSourceError):
        runtime.execute(_WINDOW_START, _WINDOW_END)


def test_runtime_records_summarizer_error_and_continues(tmp_path: Path):
    """When Summarizer.summarize raises, runtime falls back to deterministic summarizer
    and the alert is still attempted."""
    alerter = FakeAlerter(delivered=True)
    summarizer = FakeSummarizer(raise_exc=ValueError("model exploded"))
    anomaly = _make_sms_anomaly()

    with patch("opsmitra.runtime.detect_anomalies", return_value=[anomaly]):
        runtime = _make_runtime(tmp_path, summarizer=summarizer, alerter=alerter)
        result = runtime.execute(_WINDOW_START, _WINDOW_END)

    # Alert should still be attempted (fallback kicks in)
    assert len(alerter.calls) == 1
    assert result.anomalies_alerted == 1


def test_runtime_records_alert_delivery_error_in_result(tmp_path: Path):
    """When SlackAlerter.send raises, runtime captures a redacted error string (not the URL)
    and the result has 1 error entry."""
    exc = Exception(f"POST failed: {_WEBHOOK_SENTINEL}")
    alerter = FakeAlerter(raise_exc=exc)
    anomaly = _make_sms_anomaly()

    with patch("opsmitra.runtime.detect_anomalies", return_value=[anomaly]):
        runtime = _make_runtime(tmp_path, alerter=alerter)
        result = runtime.execute(_WINDOW_START, _WINDOW_END)

    assert len(result.errors) == 1
    # Webhook URL must not appear in the captured error string
    assert _WEBHOOK_SENTINEL not in result.errors[0]


# ---------------------------------------------------------------------------
# Security / log-safety tests
# ---------------------------------------------------------------------------


def test_runtime_does_not_log_webhook_url(tmp_path: Path, caplog):
    """The webhook URL sentinel must not appear in any caplog record at any level."""
    alerter = FakeAlerter(delivered=True)
    anomaly = _make_sms_anomaly()

    with patch("opsmitra.runtime.detect_anomalies", return_value=[anomaly]):
        runtime = _make_runtime(tmp_path, alerter=alerter)
        with caplog.at_level(logging.DEBUG, logger="opsmitra.runtime"):
            runtime.execute(_WINDOW_START, _WINDOW_END)

    assert _WEBHOOK_SENTINEL not in caplog.text


def test_runtime_does_not_log_raw_event_content(tmp_path: Path, caplog):
    """Raw event evidence (sentinel string) must not appear in any log record."""
    evidence_sentinel = "SUPERSECRET_EVIDENCE_PAYLOAD_XYZ"
    anomaly = Anomaly(
        id="anom_secret",
        type="sms_abuse_spike",
        severity="high",
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
        tenant_id="tenant_acme",
        subject={"endpoint": "/sms/send", "api_key_id": "key_abc"},
        observed={"count": 600},
        baseline={"hourly_count": 40},
        ratio=15.0,
        evidence={"notes": [evidence_sentinel], "sample_request_ids": ["req_001"]},
        recommended_actions=["Throttle the API key"],
    )

    with patch("opsmitra.runtime.detect_anomalies", return_value=[anomaly]):
        runtime = _make_runtime(tmp_path)
        with caplog.at_level(logging.DEBUG, logger="opsmitra.runtime"):
            runtime.execute(_WINDOW_START, _WINDOW_END)

    assert evidence_sentinel not in caplog.text


# ---------------------------------------------------------------------------
# Cost guard / max_events tests
# ---------------------------------------------------------------------------


def test_runtime_respects_max_events_per_window(tmp_path: Path, caplog):
    """When source yields more than max_events_per_window, runtime truncates to the
    limit and logs a WARNING (per plan §6 Stage 2 — truncation, not raise)."""
    # Create 5 minimal events — use max_events_per_window=3 so 2 are truncated
    ts = _WINDOW_START + timedelta(seconds=1)
    events = [
        Event(
            timestamp=ts,
            tenant_id="t1",
            endpoint="/sms/send",
            method="POST",
            status_code=200,
            cost_units=1.0,
            request_id=f"req_{i}",
        )
        for i in range(5)
    ]
    source = FakeEventSource(events=events)

    with caplog.at_level(logging.WARNING, logger="opsmitra.runtime"):
        runtime = _make_runtime(tmp_path, source=source, max_events_per_window=3)
        result = runtime.execute(_WINDOW_START, _WINDOW_END)

    # Truncation warning must be emitted
    warning_texts = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("truncat" in t.lower() for t in warning_texts)


# ---------------------------------------------------------------------------
# Cooldown persistence after execute
# ---------------------------------------------------------------------------


def test_runtime_persists_cooldown_after_execute(tmp_path: Path):
    """After a successful alert, the cooldown file on disk must contain the fingerprint."""
    cooldown_path = tmp_path / "cooldown.json"
    alerter = FakeAlerter(delivered=True)
    anomaly = _make_sms_anomaly()

    with patch("opsmitra.runtime.detect_anomalies", return_value=[anomaly]):
        runtime = _make_runtime(tmp_path, alerter=alerter)
        runtime.execute(_WINDOW_START, _WINDOW_END)

    assert cooldown_path.exists()
    data = cooldown_path.read_text()
    import json
    parsed = json.loads(data)
    assert "entries" in parsed
    assert len(parsed["entries"]) >= 1


# ---------------------------------------------------------------------------
# Orchestrator wiring
# ---------------------------------------------------------------------------


def test_runtime_uses_detect_anomalies_orchestrator(tmp_path: Path):
    """Runtime must call detect_anomalies (the orchestrator), not the public wrappers."""
    with patch("opsmitra.runtime.detect_anomalies", return_value=[]) as mock_detect:
        runtime = _make_runtime(tmp_path)
        runtime.execute(_WINDOW_START, _WINDOW_END)

    mock_detect.assert_called_once()


# ---------------------------------------------------------------------------
# Result count accuracy
# ---------------------------------------------------------------------------


def test_runtime_result_counts_are_correct(tmp_path: Path):
    """3 detected anomalies, 1 suppressed (already alerted) → alerted=2, suppressed=1."""
    now_time = _NOW
    cooldown = AnomalyCooldown(
        tmp_path / "cooldown.json",
        window_seconds=3600,
        clock=lambda: now_time,
    )
    anomaly1 = _make_sms_anomaly(tenant="t1")
    anomaly2 = _make_sms_anomaly(tenant="t2")
    anomaly3 = _make_sms_anomaly(tenant="t3")

    # Pre-alert anomaly1 so it's suppressed
    cooldown.record_alerted(anomaly1)

    alerter = FakeAlerter(delivered=True)
    runtime = Runtime(
        source=FakeEventSource(),
        summarizer=FakeSummarizer(),
        alerter=alerter,
        cooldown=cooldown,
        thresholds=DetectorThresholds(),
        clock=lambda: now_time,
    )

    with patch("opsmitra.runtime.detect_anomalies", return_value=[anomaly1, anomaly2, anomaly3]):
        result = runtime.execute(_WINDOW_START, _WINDOW_END)

    assert result.anomalies_detected == 3
    assert result.anomalies_alerted == 2
    assert result.anomalies_suppressed == 1


# ---------------------------------------------------------------------------
# Phase 4 — H2: Runtime guard for send-without-webhook
# ---------------------------------------------------------------------------


def test_runtime_rejects_send_mode_without_webhook(tmp_path: Path):
    """Runtime must raise RuntimeConfigurationError when dry_run=False and
    slack_webhook_url is None, at construction time (not per-anomaly loop)."""
    from opsmitra.config import AlertConfig
    from opsmitra.runtime import RuntimeConfigurationError
    from opsmitra.slack_alerter import SlackAlerter

    alert_cfg = AlertConfig(
        slack_webhook_url=None,
        dry_run=False,
    )
    alerter = SlackAlerter(alert_cfg)

    with pytest.raises(RuntimeConfigurationError) as exc_info:
        Runtime(
            source=FakeEventSource(),
            summarizer=FakeSummarizer(),
            alerter=alerter,
            cooldown=AnomalyCooldown(
                tmp_path / "cooldown.json",
                window_seconds=3600,
                clock=lambda: _NOW,
            ),
            thresholds=DetectorThresholds(),
            clock=lambda: _NOW,
            dry_run=False,
        )

    err_msg = str(exc_info.value)
    assert "OPSMITRA_SLACK_WEBHOOK_URL" in err_msg
    assert "https://" not in err_msg
    assert "hooks.slack.com" not in err_msg


# ---------------------------------------------------------------------------
# C1 — Baseline-ratio defense preserved when DetectorThresholds is provided
# ---------------------------------------------------------------------------


def test_runtime_thresholds_preserve_baseline_ratio_defense(tmp_path: Path):
    """Verifies C1 fix: the 5× SMS and 10× cost ratio defenses are preserved
    even when DetectorThresholds is provided to Runtime.

    Without C1 (ratio=1.0 overrides), any count/cost above the absolute
    threshold would trigger an anomaly regardless of baseline.
    With C1, the ratio must also exceed the DetectionConfig default (5× SMS,
    10× cost) before an anomaly fires.

    Scenario (SMS):
      - baseline: 20 SMS events in the baseline window (pre-window_start)
      - current:  40 SMS events in the detection window  → ratio = 2.0
      - sms_abuse_rate_per_minute=0.1 → sms_min_count = 6 for a 60-min window
      - 40 >= 6 (absolute threshold met), but ratio 2.0 < 5.0 → suppressed

    Scenario (cost):
      - baseline: $100 cost_units in baseline
      - current:  $200 cost_units in detection window → ratio = 2.0
      - cost_runaway_delta_usd=50.0 → threshold = $50
      - $200 >= $50 (absolute threshold met), but ratio 2.0 < 10.0 → suppressed
    """
    import uuid

    baseline_start = _WINDOW_START - timedelta(hours=1)

    def _sms_event(ts: datetime, *, in_baseline: bool = False) -> Event:
        return Event(
            timestamp=ts,
            tenant_id="tenant_ratio_test",
            endpoint="/sms/send",
            method="POST",
            status_code=200,
            cost_units=0.0,
            request_id=str(uuid.uuid4()),
            api_key_id="key_ratio_test",
        )

    def _cost_event(ts: datetime, cost: float) -> Event:
        return Event(
            timestamp=ts,
            tenant_id="tenant_cost_ratio",
            endpoint="/jobs/process",
            method="POST",
            status_code=200,
            cost_units=cost,
            request_id=str(uuid.uuid4()),
        )

    # Baseline SMS events (before window_start) — 20 events
    baseline_sms = [_sms_event(baseline_start + timedelta(seconds=i * 120)) for i in range(20)]
    # Current SMS events (in window) — 40 events; ratio = 40/20 = 2.0 < 5.0
    current_sms = [_sms_event(_WINDOW_START + timedelta(seconds=i * 60)) for i in range(40)]

    # Baseline cost events — total $100
    baseline_cost = [_cost_event(baseline_start + timedelta(seconds=i * 60), 50.0) for i in range(2)]
    # Current cost events — total $200; ratio = 2.0 < 10.0
    current_cost = [_cost_event(_WINDOW_START + timedelta(seconds=i * 60), 100.0) for i in range(2)]

    all_events = baseline_sms + current_sms + baseline_cost + current_cost

    # Low absolute thresholds so both SMS and cost WOULD trigger without ratio defense
    thresholds = DetectorThresholds(
        sms_abuse_rate_per_minute=0.1,   # very low: min_count ≈ 6 for 60-min window
        cost_runaway_delta_usd=50.0,     # $200 > $50 absolute → would trigger without ratio
    )

    source = FakeEventSource(events=all_events)
    alerter = FakeAlerter()
    runtime = Runtime(
        source=source,
        summarizer=FakeSummarizer(),
        alerter=alerter,
        cooldown=AnomalyCooldown(
            tmp_path / "cooldown.json",
            window_seconds=3600,
            clock=lambda: _NOW,
        ),
        thresholds=thresholds,
        clock=lambda: _NOW,
        dry_run=True,
    )

    result = runtime.execute(_WINDOW_START, _WINDOW_END)

    # Ratio defense: 2.0 < 5.0 for SMS, 2.0 < 10.0 for cost → both suppressed
    assert result.anomalies_detected == 0, (
        f"Expected 0 anomalies (ratio defense active), got {result.anomalies_detected}. "
        f"C1 fix may not have preserved the baseline-ratio defense."
    )
    assert len(alerter.calls) == 0, "No alerts should have been sent when ratio defense active"
