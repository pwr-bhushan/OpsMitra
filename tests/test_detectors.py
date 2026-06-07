import uuid
from datetime import datetime, timedelta, timezone

from opsmitra.config import DetectorThresholds
from opsmitra.detectors import DetectionConfig, detect_anomalies
from opsmitra.generator import Incident, SyntheticLogGenerator
from opsmitra.models import Event


START = datetime(2026, 5, 28, 10, tzinfo=timezone.utc)
WINDOW_START = START + timedelta(hours=1)
WINDOW_END = START + timedelta(hours=2)


def _events_with_incidents(*incidents: Incident):
    return SyntheticLogGenerator(seed=11).generate(start=START, hours=2, incidents=incidents)


def test_detects_sms_abuse_spike_with_structured_evidence():
    events = _events_with_incidents(
        Incident(kind="sms_abuse", tenant_id="tenant_acme", start=WINDOW_START, duration_minutes=15, volume=600, api_key_id="key_abused")
    )

    anomalies = detect_anomalies(events, WINDOW_START, WINDOW_END, DetectionConfig(sms_min_count=300))
    sms = next(anomaly for anomaly in anomalies if anomaly.type == "sms_abuse_spike")

    assert sms.severity == "high"
    assert sms.tenant_id == "tenant_acme"
    assert sms.observed["count"] >= 600
    assert sms.subject["api_key_id"] == "key_abused"
    assert sms.evidence["sample_request_ids"]
    assert "Temporarily throttle the API key" in sms.recommended_actions


def test_detects_auth_failure_burst_across_users():
    events = _events_with_incidents(
        Incident(kind="auth_failure_burst", tenant_id="tenant_beta", start=WINDOW_START, duration_minutes=10, volume=180, ip="198.51.100.10")
    )

    anomalies = detect_anomalies(events, WINDOW_START, WINDOW_END, DetectionConfig(auth_min_failures=100))
    auth = next(anomaly for anomaly in anomalies if anomaly.type == "auth_failure_burst")

    assert auth.severity == "high"
    assert auth.subject["ip"] == "198.51.100.10"
    assert auth.observed["failure_count"] >= 180
    assert auth.observed["distinct_users"] > 20


def test_detects_endpoint_error_rate_spike():
    events = _events_with_incidents(
        Incident(kind="error_rate_spike", tenant_id="tenant_acme", start=WINDOW_START, duration_minutes=10, volume=120, endpoint="/checkout")
    )

    anomalies = detect_anomalies(events, WINDOW_START, WINDOW_END, DetectionConfig(error_min_requests=50, error_rate_threshold=0.5))
    error = next(anomaly for anomaly in anomalies if anomaly.type == "endpoint_error_rate_spike")

    assert error.subject["endpoint"] == "/checkout"
    assert error.observed["error_count"] >= 120
    assert error.observed["error_rate"] >= 0.5
    assert "Inspect recent deploys" in error.recommended_actions


def test_detects_cost_runaway_usage():
    events = _events_with_incidents(
        Incident(kind="cost_runaway", tenant_id="tenant_acme", start=WINDOW_START, duration_minutes=10, volume=80, endpoint="/jobs/process")
    )

    anomalies = detect_anomalies(events, WINDOW_START, WINDOW_END, DetectionConfig(cost_min_units=1_000))
    cost = next(anomaly for anomaly in anomalies if anomaly.type == "cost_runaway_usage")

    assert cost.tenant_id == "tenant_acme"
    assert cost.subject["endpoint"] == "/jobs/process"
    assert cost.observed["cost_units"] >= 2_000
    assert "Pause or throttle the workload" in cost.recommended_actions


def test_normal_baseline_does_not_emit_high_signal_anomalies():
    events = SyntheticLogGenerator(seed=13).generate(start=START, hours=2)

    anomalies = detect_anomalies(events, WINDOW_START, WINDOW_END)

    assert anomalies == []


# ---------------------------------------------------------------------------
# Step 10 M5 — window_seconds honored
# ---------------------------------------------------------------------------


def _auth_event(ts: datetime, ip: str = "1.2.3.4", user_id: str = "u1") -> Event:
    return Event(
        timestamp=ts,
        tenant_id="tenant_acme",
        endpoint="/auth/login",
        method="POST",
        status_code=401,
        cost_units=0.0,
        request_id=str(uuid.uuid4()),
        ip=ip,
        user_id=user_id,
    )


def _cost_event(ts: datetime, cost_units: float = 500.0) -> Event:
    return Event(
        timestamp=ts,
        tenant_id="tenant_acme",
        endpoint="/jobs/process",
        method="POST",
        status_code=200,
        cost_units=cost_units,
        request_id=str(uuid.uuid4()),
    )


def test_auth_burst_window_seconds_honored():
    """With auth_burst_window_seconds=30, only events within 30 s of each other
    form a burst cluster. Events spread over 120 s with a 30 s window must not
    all count toward the same burst.

    RED until M5 wires auth_burst_window_seconds into _detect_auth_failure_burst.
    """
    # 6 events spread across 120 s: pairs at t=0,t=10 | t=60,t=70 | t=110,t=120
    # With window=30s: no single 30-second slice contains enough to trip count=3
    events = [
        _auth_event(WINDOW_START + timedelta(seconds=0),  user_id="u1"),
        _auth_event(WINDOW_START + timedelta(seconds=10), user_id="u2"),
        _auth_event(WINDOW_START + timedelta(seconds=60), user_id="u3"),
        _auth_event(WINDOW_START + timedelta(seconds=70), user_id="u4"),
        _auth_event(WINDOW_START + timedelta(seconds=110), user_id="u5"),
        _auth_event(WINDOW_START + timedelta(seconds=120), user_id="u6"),
    ]

    thresholds = DetectorThresholds(auth_burst_count=3, auth_burst_window_seconds=30)
    # RED: thresholds kwarg + window_seconds wiring both missing
    anomalies = detect_anomalies(events, WINDOW_START, WINDOW_END, thresholds=thresholds)
    auth_anomalies = [a for a in anomalies if a.type == "auth_failure_burst"]

    # No 30-second window contains ≥3 events from the same IP → no burst
    assert auth_anomalies == [], (
        f"Expected no auth_failure_burst with 30s window on spread events, "
        f"got {len(auth_anomalies)} anomaly/ies"
    )


def test_cost_runaway_window_seconds_honored():
    """With cost_runaway_window_seconds=60, cost events must be clustered within
    a 60 s window to form a runaway. Events spread over 300 s should not trigger.

    RED until M5 wires cost_runaway_window_seconds into _detect_cost_runaway.
    """
    # 5 cost events spaced 90 s apart: no two consecutive within 60 s
    # Total cost = 5 × 500 = 2500 > delta_usd=10, but no 60 s window clusters them
    events = [
        _cost_event(WINDOW_START + timedelta(seconds=0),   cost_units=500.0),
        _cost_event(WINDOW_START + timedelta(seconds=90),  cost_units=500.0),
        _cost_event(WINDOW_START + timedelta(seconds=180), cost_units=500.0),
        _cost_event(WINDOW_START + timedelta(seconds=270), cost_units=500.0),
        _cost_event(WINDOW_START + timedelta(seconds=360), cost_units=500.0),
    ]

    thresholds = DetectorThresholds(cost_runaway_delta_usd=10.0, cost_runaway_window_seconds=60)
    # RED: thresholds kwarg + window_seconds wiring both missing
    anomalies = detect_anomalies(events, WINDOW_START, WINDOW_END, thresholds=thresholds)
    cost_anomalies = [a for a in anomalies if a.type == "cost_runaway_usage"]

    # No 60-second window accumulates enough cost → no runaway
    assert cost_anomalies == [], (
        f"Expected no cost_runaway_usage with 60s window on spread events, "
        f"got {len(cost_anomalies)} anomaly/ies"
    )
