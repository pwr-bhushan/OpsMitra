from datetime import datetime, timedelta, timezone

from opsmitra.generator import Incident, SyntheticLogGenerator, write_jsonl
from opsmitra.models import Event


START = datetime(2026, 5, 28, 10, tzinfo=timezone.utc)


def test_generator_is_deterministic_for_same_seed():
    first = SyntheticLogGenerator(seed=7).generate(start=START, hours=1)
    second = SyntheticLogGenerator(seed=7).generate(start=START, hours=1)

    assert [event.to_dict() for event in first] == [event.to_dict() for event in second]


def test_generator_emits_schema_valid_events():
    events = SyntheticLogGenerator(seed=1).generate(start=START, hours=1)

    assert events
    assert all(isinstance(event, Event) for event in events)
    assert all(event.timestamp.tzinfo == timezone.utc for event in events)
    assert all(event.tenant_id for event in events)
    assert all(event.request_id for event in events)


def test_sms_abuse_incident_injects_high_volume_sms_events():
    incident = Incident(
        kind="sms_abuse",
        tenant_id="tenant_acme",
        start=START + timedelta(minutes=15),
        duration_minutes=10,
        volume=250,
        api_key_id="key_abused",
    )

    events = SyntheticLogGenerator(seed=3).generate(start=START, hours=1, incidents=[incident])
    injected = [event for event in events if event.tenant_id == "tenant_acme" and event.api_key_id == "key_abused"]

    assert len(injected) == 250
    assert all(event.endpoint == "/sms/send" for event in injected)
    assert all(event.provider == "twilio" for event in injected)


def test_auth_failure_incident_spreads_failures_across_users():
    incident = Incident(
        kind="auth_failure_burst",
        tenant_id="tenant_beta",
        start=START,
        duration_minutes=5,
        volume=120,
        ip="198.51.100.10",
    )

    events = SyntheticLogGenerator(seed=4).generate(start=START, hours=1, incidents=[incident])
    injected = [event for event in events if event.ip == "198.51.100.10" and event.status_code == 401]

    assert len(injected) == 120
    assert len({event.user_id for event in injected}) > 20
    assert all(event.endpoint == "/auth/login" for event in injected)


def test_error_spike_and_cost_runaway_incidents_are_injectable(tmp_path):
    incidents = [
        Incident(kind="error_rate_spike", tenant_id="tenant_acme", start=START, duration_minutes=5, volume=80, endpoint="/checkout"),
        Incident(kind="cost_runaway", tenant_id="tenant_acme", start=START, duration_minutes=5, volume=50, endpoint="/jobs/process"),
    ]

    events = SyntheticLogGenerator(seed=9).generate(start=START, hours=1, incidents=incidents)
    errors = [event for event in events if event.endpoint == "/checkout" and event.status_code >= 500]
    costly = [event for event in events if event.endpoint == "/jobs/process" and event.cost_units >= 25]

    assert len(errors) == 80
    assert len(costly) == 50

    output = tmp_path / "events.jsonl"
    write_jsonl(events, output)

    lines = output.read_text().strip().splitlines()
    assert len(lines) == len(events)
