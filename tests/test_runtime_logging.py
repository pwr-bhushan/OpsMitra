"""RED-phase structured-logging tests for opsmitra.runtime.

All tests here use caplog to assert that the runtime pipeline emits the
required structured key=value log events and NEVER leaks secrets.

Imports from opsmitra.runtime are expected to fail at collection until
src/opsmitra/runtime.py is implemented (Step 8, Phase C).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence
from unittest.mock import patch

import pytest

from opsmitra.config import DetectorThresholds
from opsmitra.models import Anomaly, Event
from opsmitra.summarizer import AlertSummary
from opsmitra.slack_alerter import DeliveryResult

# Expected to fail until runtime module exists.
from opsmitra.runtime import Runtime, RuntimeResult  # noqa: E402
from opsmitra.cooldown import AnomalyCooldown


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_WINDOW_START = datetime(2026, 6, 7, 11, 0, 0, tzinfo=timezone.utc)
_WINDOW_END = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)
_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)

_WEBHOOK_SENTINEL = "https://hooks.slack.com/services/LOGGING-SENTINEL-UNIQUE"
_AWS_ACCOUNT_SENTINEL = "123456789012"
_RAW_EVENT_SENTINEL = "RAW_PAYLOAD_CONTENT_SENTINEL"


# ---------------------------------------------------------------------------
# Fake collaborators (mirrored from test_runtime.py)
# ---------------------------------------------------------------------------


class FakeEventSource:
    def __init__(self, events: list[Event] | None = None) -> None:
        self._events = events or []

    def fetch_events(
        self,
        window_start: datetime,
        window_end: datetime,
        tenant: str | None = None,
        types: Sequence[str] | None = None,
    ) -> Iterable[Event]:
        return iter(self._events)


class FakeSummarizer:
    def summarize(self, anomaly: Anomaly) -> AlertSummary:
        return AlertSummary(
            title="Fake alert",
            severity=anomaly.severity,
            summary="Fake summary.",
            likely_cause="Fake cause.",
            recommended_action="Fake action.",
            confidence="low",
        )


class FakeAlerter:
    def send(self, summary: AlertSummary, anomaly: Anomaly) -> DeliveryResult:
        return DeliveryResult(
            delivered=True,
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
        id="anom_logging_1",
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


def _make_runtime(tmp_path: Path) -> Runtime:
    return Runtime(
        source=FakeEventSource(),
        summarizer=FakeSummarizer(),
        alerter=FakeAlerter(),
        cooldown=AnomalyCooldown(
            tmp_path / "cooldown.json",
            window_seconds=3600,
            clock=lambda: _NOW,
        ),
        thresholds=DetectorThresholds(),
        clock=lambda: _NOW,
    )


# ---------------------------------------------------------------------------
# Test 1: Stage logs emitted
# ---------------------------------------------------------------------------


def test_runtime_emits_stage_logs(tmp_path: Path, caplog):
    """caplog must contain structured log records for each pipeline stage.

    Expected event_type tokens (key=value format):
    - event=runtime_start
    - event=events_fetched
    - event=detection_complete
    - event=runtime_done
    """
    anomaly = _make_sms_anomaly()

    with patch("opsmitra.runtime.detect_anomalies", return_value=[anomaly]):
        runtime = _make_runtime(tmp_path)
        with caplog.at_level(logging.INFO, logger="opsmitra.runtime"):
            runtime.execute(_WINDOW_START, _WINDOW_END)

    all_log_text = caplog.text
    required_events = [
        "event=runtime_start",
        "event=events_fetched",
        "event=detection_complete",
        "event=runtime_done",
    ]
    for event_token in required_events:
        assert event_token in all_log_text, (
            f"Expected structured log token '{event_token}' not found in caplog.\n"
            f"Captured logs:\n{all_log_text}"
        )


# ---------------------------------------------------------------------------
# Test 2: No secrets in logs
# ---------------------------------------------------------------------------


def test_runtime_logs_no_secrets(tmp_path: Path, caplog):
    """Webhook URL sentinel, AWS account sentinel, and raw event sentinel must not
    appear in any log record at any level during pipeline execution."""
    anomaly = _make_sms_anomaly()

    with patch("opsmitra.runtime.detect_anomalies", return_value=[anomaly]):
        runtime = _make_runtime(tmp_path)
        with caplog.at_level(logging.DEBUG, logger="opsmitra.runtime"):
            runtime.execute(_WINDOW_START, _WINDOW_END)

    for sentinel in (_WEBHOOK_SENTINEL, _AWS_ACCOUNT_SENTINEL, _RAW_EVENT_SENTINEL):
        assert sentinel not in caplog.text, (
            f"Secret sentinel '{sentinel}' leaked into runtime logs."
        )


# ---------------------------------------------------------------------------
# Test 3: Event fetch log contains count, not events
# ---------------------------------------------------------------------------


def test_runtime_logs_counts_only_for_events(tmp_path: Path, caplog):
    """The events_fetched log line must contain a count field, not raw event data.

    The raw request_id appearing verbatim in logs would be a privacy violation.
    """
    ts = _WINDOW_START + timedelta(seconds=1)
    events = [
        Event(
            timestamp=ts,
            tenant_id="t1",
            endpoint="/sms/send",
            method="POST",
            status_code=200,
            cost_units=1.0,
            request_id="PRIVATE_REQUEST_ID_SENTINEL",
        )
    ]
    source = FakeEventSource(events=events)

    with patch("opsmitra.runtime.detect_anomalies", return_value=[]):
        runtime = Runtime(
            source=source,
            summarizer=FakeSummarizer(),
            alerter=FakeAlerter(),
            cooldown=AnomalyCooldown(
                tmp_path / "cooldown.json",
                window_seconds=3600,
                clock=lambda: _NOW,
            ),
            thresholds=DetectorThresholds(),
            clock=lambda: _NOW,
        )
        with caplog.at_level(logging.INFO, logger="opsmitra.runtime"):
            runtime.execute(_WINDOW_START, _WINDOW_END)

    # The fetch log must contain a count
    fetch_records = [r for r in caplog.records if "events_fetched" in r.getMessage()]
    assert len(fetch_records) >= 1, "Expected at least one events_fetched log record"
    fetch_text = fetch_records[0].getMessage()
    assert "count=" in fetch_text, f"Expected 'count=' in fetch log, got: {fetch_text!r}"

    # The request_id must never appear verbatim
    assert "PRIVATE_REQUEST_ID_SENTINEL" not in caplog.text


# ---------------------------------------------------------------------------
# Test 4: Error logs use safe (redacted) messages
# ---------------------------------------------------------------------------


def test_runtime_error_logs_use_safe_messages(tmp_path: Path, caplog):
    """When the alerter raises with a message containing the webhook URL, the
    runtime's error log entry must use a redacted/safe version (no URL)."""

    class WebhookLeakingAlerter:
        """Raises with the webhook URL embedded in the message."""

        def send(self, summary: AlertSummary, anomaly: Anomaly) -> DeliveryResult:
            raise RuntimeError(f"POST failed: {_WEBHOOK_SENTINEL}")

    anomaly = _make_sms_anomaly()

    with patch("opsmitra.runtime.detect_anomalies", return_value=[anomaly]):
        runtime = Runtime(
            source=FakeEventSource(),
            summarizer=FakeSummarizer(),
            alerter=WebhookLeakingAlerter(),
            cooldown=AnomalyCooldown(
                tmp_path / "cooldown.json",
                window_seconds=3600,
                clock=lambda: _NOW,
            ),
            thresholds=DetectorThresholds(),
            clock=lambda: _NOW,
        )
        with caplog.at_level(logging.DEBUG, logger="opsmitra.runtime"):
            result = runtime.execute(_WINDOW_START, _WINDOW_END)

    # Result must record an error
    assert len(result.errors) >= 1

    # The webhook sentinel must NOT appear anywhere in the logs
    assert _WEBHOOK_SENTINEL not in caplog.text, (
        f"Webhook URL sentinel leaked into error log records.\n"
        f"Captured log text:\n{caplog.text}"
    )
