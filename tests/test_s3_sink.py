"""RED tests for S3NDJSONEventSink using botocore.stub.Stubber.

All tests skip if botocore.stub is unavailable.
No real AWS credentials or network calls are made.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

botocore_stub = pytest.importorskip("botocore.stub")

import botocore.session  # noqa: E402

# RED: these imports fail until the module is implemented
from opsmitra.aws.s3_sink import S3NDJSONEventSink  # noqa: E402
from opsmitra.event_source import WriteResult  # noqa: E402
from opsmitra.models import Event  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_s3_client():
    session = botocore.session.get_session()
    return session.create_client("s3", region_name="us-east-1")


def _make_event(
    i: int = 0,
    *,
    tenant_id: str = "tenant_acme",
    endpoint: str = "/sms/send",
    ts: datetime | None = None,
) -> Event:
    default_ts = datetime(2026, 6, 6, 18, 0, 0, tzinfo=timezone.utc)
    return Event(
        timestamp=ts or default_ts,
        tenant_id=tenant_id,
        endpoint=endpoint,
        method="POST",
        status_code=200,
        cost_units=1.0,
        request_id=f"req_{i:04d}",
    )


_BUCKET = "opsmitra-test-bucket"
_PREFIX = "opsmitra-events"

# Expected partition key for default event (tenant_acme, 2026-06-06 18:00)
_EXPECTED_PARTITION = "tenant=tenant_acme/year=2026/month=06/day=06/hour=18"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s3_sink_put_object_success():
    """Stubber accepts put_object; WriteResult.written equals event count; partition is formatted correctly."""
    client = _make_s3_client()
    stubber = botocore_stub.Stubber(client)

    events = [_make_event(i) for i in range(3)]

    stubber.add_response(
        "put_object",
        {},  # S3 put_object returns an empty dict on success
        expected_params=None,  # relax params to avoid key ordering issues
    )

    with stubber:
        sink = S3NDJSONEventSink(client=client, bucket=_BUCKET, prefix=_PREFIX)
        result = sink.write_events(events)

    assert isinstance(result, WriteResult)
    assert result.written == 3
    assert len(result.partitions) == 1
    assert _EXPECTED_PARTITION in result.partitions[0]


def test_s3_sink_empty_events_no_put_object_call():
    """Writing empty events must not call put_object and must return WriteResult(0, [])."""
    client = _make_s3_client()
    stubber = botocore_stub.Stubber(client)
    # No responses queued — any call would raise UnStubbedResponseError

    with stubber:
        sink = S3NDJSONEventSink(client=client, bucket=_BUCKET, prefix=_PREFIX)
        result = sink.write_events([])

    assert result.written == 0
    assert result.partitions == []
    stubber.assert_no_pending_responses()


def test_s3_sink_multi_partition_events():
    """Events from two different hours must produce two put_object calls and two partition entries."""
    client = _make_s3_client()
    stubber = botocore_stub.Stubber(client)

    ts_hour18 = datetime(2026, 6, 6, 18, 0, 0, tzinfo=timezone.utc)
    ts_hour19 = datetime(2026, 6, 6, 19, 0, 0, tzinfo=timezone.utc)

    events = [
        _make_event(0, ts=ts_hour18),
        _make_event(1, ts=ts_hour18),
        _make_event(2, ts=ts_hour19),
    ]

    # Two put_object calls expected (one per hour-partition)
    stubber.add_response("put_object", {}, expected_params=None)
    stubber.add_response("put_object", {}, expected_params=None)

    with stubber:
        sink = S3NDJSONEventSink(client=client, bucket=_BUCKET, prefix=_PREFIX)
        result = sink.write_events(events)

    assert result.written == 3
    assert len(result.partitions) == 2

    partition_strs = " ".join(result.partitions)
    assert "hour=18" in partition_strs
    assert "hour=19" in partition_strs

    stubber.assert_no_pending_responses()


def test_s3_sink_missing_bucket_raises_value_error():
    """Constructing S3NDJSONEventSink without a bucket name must raise ValueError.

    Plan §7: 'raise ValueError at sink construction, not at config load' for missing bucket.
    """
    client = _make_s3_client()

    with pytest.raises(ValueError):
        S3NDJSONEventSink(client=client, bucket=None, prefix=_PREFIX)  # type: ignore[arg-type]


def test_s3_sink_missing_bucket_empty_string_raises_value_error():
    """An empty string bucket must also raise ValueError."""
    client = _make_s3_client()

    with pytest.raises(ValueError):
        S3NDJSONEventSink(client=client, bucket="", prefix=_PREFIX)


def test_s3_sink_write_result_partition_format():
    """Partition strings must follow 'tenant=X/year=YYYY/month=MM/day=DD/hour=HH' format."""
    client = _make_s3_client()
    stubber = botocore_stub.Stubber(client)

    events = [_make_event(0)]
    stubber.add_response("put_object", {}, expected_params=None)

    with stubber:
        sink = S3NDJSONEventSink(client=client, bucket=_BUCKET, prefix=_PREFIX)
        result = sink.write_events(events)

    assert len(result.partitions) == 1
    partition = result.partitions[0]
    # Must match the S3 prefix layout from plan §4
    assert "tenant=" in partition
    assert "year=" in partition
    assert "month=" in partition
    assert "day=" in partition
    assert "hour=" in partition


# ---------------------------------------------------------------------------
# Step 7 fix-pass: Phase 4 (H4, H5) tests
# ---------------------------------------------------------------------------


def test_s3_object_key_includes_batch_id():
    """The S3 PUT key must embed a 12-hex-char batch_id: events-<batch_id>.jsonl.gz."""
    import re

    client = _make_s3_client()
    stubber = botocore_stub.Stubber(client)
    captured_keys: list[str] = []

    # Use a before-send event to capture the actual key
    events = [_make_event(0)]

    # We'll capture via Stubber expected_params=None then inspect via a handler
    stubber.add_response("put_object", {}, expected_params=None)

    with stubber:
        sink = S3NDJSONEventSink(client=client, bucket=_BUCKET, prefix=_PREFIX)

        # Monkey-patch put_object to capture key
        original_put = sink._client.put_object

        def capturing_put(**kwargs):
            captured_keys.append(kwargs.get("Key", ""))
            return original_put(**kwargs)

        sink._client.put_object = capturing_put
        sink.write_events(events)

    assert len(captured_keys) == 1
    key = captured_keys[0]
    assert re.search(r"events-[0-9a-f]{12}\.jsonl\.gz$", key), (
        f"Key does not match expected pattern: {key!r}"
    )


def test_s3_rejects_oversized_partition(monkeypatch):
    """A partition body exceeding _MAX_GZIP_BYTES must raise ValueError before calling put_object."""
    import opsmitra.aws.s3_sink as s3_module

    monkeypatch.setattr(s3_module, "_MAX_GZIP_BYTES", 10)
    monkeypatch.setattr(s3_module, "_GZIP_WARN_BYTES", 5)

    client = _make_s3_client()
    stubber = botocore_stub.Stubber(client)
    # No responses queued — any put_object call would fail

    with stubber:
        sink = S3NDJSONEventSink(client=client, bucket=_BUCKET, prefix=_PREFIX)
        with pytest.raises(ValueError, match="exceeds"):
            sink.write_events([_make_event(0)])
