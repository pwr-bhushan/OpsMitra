from datetime import datetime, timedelta, timezone

from opsmitra.detectors import DetectionConfig, detect_anomalies
from opsmitra.generator import Incident, SyntheticLogGenerator


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
