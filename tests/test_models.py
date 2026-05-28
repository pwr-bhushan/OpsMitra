from datetime import datetime, timezone

import pytest

from opsmitra.models import Anomaly, Event


def test_event_from_dict_normalizes_required_fields():
    event = Event.from_dict(
        {
            "timestamp": "2026-05-28T10:15:00Z",
            "tenant_id": "tenant_acme",
            "user_id": "user_123",
            "api_key_id": "key_sms_prod",
            "endpoint": "/sms/send",
            "method": "POST",
            "status_code": 200,
            "latency_ms": 82,
            "ip": "203.0.113.10",
            "country": "IN",
            "cost_units": 1,
            "provider": "twilio",
            "request_id": "req_123",
        }
    )

    assert event.timestamp == datetime(2026, 5, 28, 10, 15, tzinfo=timezone.utc)
    assert event.tenant_id == "tenant_acme"
    assert event.endpoint == "/sms/send"
    assert event.status_code == 200
    assert event.cost_units == 1.0


def test_event_rejects_invalid_status_and_negative_cost():
    payload = {
        "timestamp": "2026-05-28T10:15:00Z",
        "tenant_id": "tenant_acme",
        "endpoint": "/sms/send",
        "method": "POST",
        "status_code": 99,
        "cost_units": -1,
        "request_id": "req_123",
    }

    with pytest.raises(ValueError):
        Event.from_dict(payload)


def test_anomaly_from_dict_validates_severity_and_actions():
    anomaly = Anomaly.from_dict(
        {
            "id": "anom_1",
            "type": "sms_abuse_spike",
            "severity": "high",
            "window_start": "2026-05-28T10:00:00Z",
            "window_end": "2026-05-28T11:00:00Z",
            "tenant_id": "tenant_acme",
            "subject": {"api_key_id": "key_sms_prod"},
            "observed": {"count": 5021},
            "baseline": {"p95": 91},
            "ratio": 55.17,
            "evidence": {"sample_request_ids": ["req_001"]},
            "recommended_actions": ["Temporarily throttle the API key"],
        }
    )

    assert anomaly.severity == "high"
    assert anomaly.recommended_actions == ["Temporarily throttle the API key"]
    assert anomaly.window_start == datetime(2026, 5, 28, 10, tzinfo=timezone.utc)


def test_anomaly_rejects_invalid_severity():
    with pytest.raises(ValueError):
        Anomaly.from_dict(
            {
                "id": "anom_1",
                "type": "sms_abuse_spike",
                "severity": "urgent",
                "window_start": "2026-05-28T10:00:00Z",
                "window_end": "2026-05-28T11:00:00Z",
                "subject": {},
                "observed": {},
                "evidence": {},
                "recommended_actions": ["Inspect"],
            }
        )
