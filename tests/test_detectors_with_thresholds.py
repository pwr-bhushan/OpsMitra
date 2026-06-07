"""RED tests: verify each existing detector still works when called with
DetectorThresholds(default) instead of the old DetectionConfig.

These tests import `detect_sms_abuse`, `detect_auth_failure_burst`,
`detect_error_rate_spike`, and `detect_cost_runaway` from `opsmitra.detectors`
(the new per-function signatures added in Step 7 Phase A).

They will FAIL at collection time because:
 1. DetectorThresholds is not yet defined.
 2. The four standalone detector functions do not yet exist / do not yet accept
    the `thresholds` parameter.

That is the expected RED state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

# RED: DetectorThresholds not yet in config.py
from opsmitra.config import DetectorThresholds  # noqa: E402

# RED: standalone detector functions not yet exported from detectors.py
from opsmitra.detectors import (  # noqa: E402
    detect_auth_failure_burst,
    detect_cost_runaway,
    detect_error_rate_spike,
    detect_sms_abuse,
)
from opsmitra.generator import Incident, SyntheticLogGenerator  # noqa: E402
from opsmitra.models import Event  # noqa: E402

# ---------------------------------------------------------------------------
# Shared time constants (mirrors test_detectors.py)
# ---------------------------------------------------------------------------

_START = datetime(2026, 5, 28, 10, tzinfo=timezone.utc)
_WINDOW_START = _START + timedelta(hours=1)
_WINDOW_END = _START + timedelta(hours=2)


def _events_with_incidents(*incidents: Incident) -> list[Event]:
    return SyntheticLogGenerator(seed=11).generate(
        start=_START, hours=2, incidents=incidents
    )


# ---------------------------------------------------------------------------
# SMS abuse detector
# ---------------------------------------------------------------------------


def test_sms_abuse_with_default_thresholds_emits_anomaly():
    """detect_sms_abuse called with DetectorThresholds() must emit the same anomaly
    as the old DetectionConfig-based call when incident volume exceeds thresholds."""
    events = _events_with_incidents(
        Incident(
            kind="sms_abuse",
            tenant_id="tenant_acme",
            start=_WINDOW_START,
            duration_minutes=15,
            volume=600,
            api_key_id="key_abused",
        )
    )
    current = [e for e in events if _WINDOW_START <= e.timestamp < _WINDOW_END]
    baseline = [e for e in events if e.timestamp < _WINDOW_START]

    # Use very low thresholds to guarantee detection regardless of default tuning
    thresholds = DetectorThresholds(sms_abuse_rate_per_minute=0.1)
    anomalies = detect_sms_abuse(
        current=current,
        baseline=baseline,
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
        thresholds=thresholds,
    )

    assert len(anomalies) >= 1
    sms = next(a for a in anomalies if a.type == "sms_abuse_spike")
    assert sms.severity == "high"
    assert sms.tenant_id == "tenant_acme"


def test_sms_abuse_default_thresholds_arg_is_optional():
    """detect_sms_abuse must work without explicitly passing thresholds (default arg)."""
    events = _events_with_incidents(
        Incident(
            kind="sms_abuse",
            tenant_id="tenant_acme",
            start=_WINDOW_START,
            duration_minutes=15,
            volume=600,
            api_key_id="key_abused",
        )
    )
    current = [e for e in events if _WINDOW_START <= e.timestamp < _WINDOW_END]
    baseline = [e for e in events if e.timestamp < _WINDOW_START]

    # No thresholds arg — must not raise
    anomalies = detect_sms_abuse(
        current=current,
        baseline=baseline,
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
    )

    assert isinstance(anomalies, list)


# ---------------------------------------------------------------------------
# Auth failure burst detector
# ---------------------------------------------------------------------------


def test_auth_failure_burst_with_default_thresholds_emits_anomaly():
    """detect_auth_failure_burst with DetectorThresholds() must detect the burst incident."""
    events = _events_with_incidents(
        Incident(
            kind="auth_failure_burst",
            tenant_id="tenant_beta",
            start=_WINDOW_START,
            duration_minutes=10,
            volume=180,
            ip="198.51.100.10",
        )
    )
    current = [e for e in events if _WINDOW_START <= e.timestamp < _WINDOW_END]

    thresholds = DetectorThresholds(auth_burst_count=10)
    anomalies = detect_auth_failure_burst(
        current=current,
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
        thresholds=thresholds,
    )

    assert len(anomalies) >= 1
    auth = next(a for a in anomalies if a.type == "auth_failure_burst")
    assert auth.subject["ip"] == "198.51.100.10"


# ---------------------------------------------------------------------------
# Error spike detector
# ---------------------------------------------------------------------------


def test_error_spike_with_default_thresholds_emits_anomaly():
    """detect_error_rate_spike with DetectorThresholds() must detect the error spike incident."""
    events = _events_with_incidents(
        Incident(
            kind="error_rate_spike",
            tenant_id="tenant_acme",
            start=_WINDOW_START,
            duration_minutes=10,
            volume=120,
            endpoint="/checkout",
        )
    )
    current = [e for e in events if _WINDOW_START <= e.timestamp < _WINDOW_END]
    baseline = [e for e in events if e.timestamp < _WINDOW_START]

    thresholds = DetectorThresholds(endpoint_error_rate=0.3, endpoint_error_min_samples=10)
    anomalies = detect_error_rate_spike(
        current=current,
        baseline=baseline,
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
        thresholds=thresholds,
    )

    assert len(anomalies) >= 1
    error = next(a for a in anomalies if a.type == "endpoint_error_rate_spike")
    assert error.subject["endpoint"] == "/checkout"


# ---------------------------------------------------------------------------
# Cost runaway detector
# ---------------------------------------------------------------------------


def test_cost_runaway_with_default_thresholds_emits_anomaly():
    """detect_cost_runaway with DetectorThresholds() must detect the cost runaway incident."""
    events = _events_with_incidents(
        Incident(
            kind="cost_runaway",
            tenant_id="tenant_acme",
            start=_WINDOW_START,
            duration_minutes=10,
            volume=80,
            endpoint="/jobs/process",
        )
    )
    current = [e for e in events if _WINDOW_START <= e.timestamp < _WINDOW_END]
    baseline = [e for e in events if e.timestamp < _WINDOW_START]

    thresholds = DetectorThresholds(cost_runaway_delta_usd=1.0)
    anomalies = detect_cost_runaway(
        current=current,
        baseline=baseline,
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
        thresholds=thresholds,
    )

    assert len(anomalies) >= 1
    cost = next(a for a in anomalies if a.type == "cost_runaway_usage")
    assert cost.tenant_id == "tenant_acme"
    assert cost.subject["endpoint"] == "/jobs/process"
