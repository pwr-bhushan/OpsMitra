"""RED tests for EventSource / EventSink protocols and local NDJSON implementations.

All imports from opsmitra.event_source are expected to fail at collection time
until the module is implemented (that is the desired RED state).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from opsmitra.models import Event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 6, 6, 18, 0, 0, tzinfo=timezone.utc)


def _make_event(
    i: int = 0,
    *,
    tenant_id: str = "tenant_acme",
    endpoint: str = "/sms/send",
    ts: datetime | None = None,
) -> Event:
    return Event(
        timestamp=ts or _TS,
        tenant_id=tenant_id,
        endpoint=endpoint,
        method="POST",
        status_code=200,
        cost_units=1.0,
        request_id=f"req_{i:04d}",
    )


# ---------------------------------------------------------------------------
# Imports under test (will fail until implemented — RED state)
# ---------------------------------------------------------------------------

from opsmitra.event_source import (  # noqa: E402
    EventSink,
    EventSource,
    LocalNDJSONEventSink,
    LocalNDJSONEventSource,
    WriteResult,
)


# ---------------------------------------------------------------------------
# Protocol shape
# ---------------------------------------------------------------------------


def test_event_source_protocol_is_importable():
    """EventSource Protocol must be importable from opsmitra.event_source."""
    assert EventSource is not None


def test_event_sink_protocol_is_importable():
    """EventSink Protocol must be importable from opsmitra.event_source."""
    assert EventSink is not None


def test_write_result_is_dataclass_with_written_and_partitions():
    """WriteResult must be a frozen dataclass with .written (int) and .partitions (list[str])."""
    result = WriteResult(written=3, partitions=["local"])
    assert result.written == 3
    assert result.partitions == ["local"]


def test_write_result_is_immutable():
    """WriteResult must be frozen (immutable)."""
    result = WriteResult(written=1, partitions=["local"])
    with pytest.raises((AttributeError, TypeError)):
        result.written = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LocalNDJSONEventSink — write_events
# ---------------------------------------------------------------------------


def test_local_sink_writes_valid_ndjson(tmp_path: Path):
    """write_events must produce a file where every line is valid JSON."""
    sink = LocalNDJSONEventSink(tmp_path / "events.jsonl")
    events = [_make_event(i) for i in range(3)]
    sink.write_events(events)

    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert len(lines) == 3
    for line in lines:
        parsed = json.loads(line)
        assert "timestamp" in parsed
        assert "tenant_id" in parsed


def test_local_sink_write_result_written_count(tmp_path: Path):
    """WriteResult.written must equal the number of events passed."""
    sink = LocalNDJSONEventSink(tmp_path / "events.jsonl")
    events = [_make_event(i) for i in range(5)]
    result = sink.write_events(events)

    assert result.written == 5


def test_local_sink_write_result_partitions_is_local(tmp_path: Path):
    """Local sink's WriteResult.partitions must contain exactly ['local']."""
    sink = LocalNDJSONEventSink(tmp_path / "events.jsonl")
    result = sink.write_events([_make_event(0)])

    assert result.partitions == ["local"]


def test_local_sink_trailing_newline(tmp_path: Path):
    """NDJSON file produced by local sink must end with a trailing newline."""
    sink = LocalNDJSONEventSink(tmp_path / "events.jsonl")
    sink.write_events([_make_event(0)])

    raw = (tmp_path / "events.jsonl").read_bytes()
    assert raw.endswith(b"\n")


def test_local_sink_empty_events_write_result(tmp_path: Path):
    """Writing an empty list must return WriteResult(written=0, partitions=['local'])."""
    sink = LocalNDJSONEventSink(tmp_path / "events.jsonl")
    result = sink.write_events([])

    assert result.written == 0
    # partitions still reports ['local'] for empty writes (sink identity)
    assert result.partitions == ["local"]


# ---------------------------------------------------------------------------
# LocalNDJSONEventSource — fetch_events
# ---------------------------------------------------------------------------


def test_local_source_round_trip(tmp_path: Path):
    """Sink-then-source round-trip must return the original events (order-insensitive)."""
    path = tmp_path / "events.jsonl"
    events = [_make_event(i) for i in range(5)]

    sink = LocalNDJSONEventSink(path)
    sink.write_events(events)

    source = LocalNDJSONEventSource(path)
    fetched = list(
        source.fetch_events(
            window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            window_end=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
    )

    assert len(fetched) == 5
    original_ids = {e.request_id for e in events}
    fetched_ids = {e.request_id for e in fetched}
    assert original_ids == fetched_ids


def test_local_source_empty_file_returns_no_events(tmp_path: Path):
    """Reading a nonexistent path must return an empty iterable (not raise)."""
    source = LocalNDJSONEventSource(tmp_path / "nonexistent.jsonl")
    fetched = list(
        source.fetch_events(
            window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            window_end=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
    )
    assert fetched == []


def test_local_source_file_not_found_returns_empty(tmp_path: Path):
    """A missing file should return empty events (graceful degradation)."""
    source = LocalNDJSONEventSource(tmp_path / "does_not_exist.jsonl")
    result = list(
        source.fetch_events(
            window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            window_end=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
    )
    assert result == []


def test_local_source_time_window_filtering(tmp_path: Path):
    """fetch_events must filter events outside [window_start, window_end)."""
    from datetime import timedelta

    path = tmp_path / "events.jsonl"

    t0 = datetime(2026, 6, 6, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 6, 11, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)

    events = [
        _make_event(0, ts=t0),
        _make_event(1, ts=t1),
        _make_event(2, ts=t2),
    ]
    LocalNDJSONEventSink(path).write_events(events)

    source = LocalNDJSONEventSource(path)
    # Only t1 falls in [t1, t2)
    fetched = list(source.fetch_events(window_start=t1, window_end=t2))
    assert len(fetched) == 1
    assert fetched[0].request_id == "req_0001"


def test_local_source_tenant_filter(tmp_path: Path):
    """fetch_events with tenant filter must exclude other tenants."""
    path = tmp_path / "events.jsonl"
    events = [
        _make_event(0, tenant_id="tenant_acme"),
        _make_event(1, tenant_id="tenant_beta"),
        _make_event(2, tenant_id="tenant_acme"),
    ]
    LocalNDJSONEventSink(path).write_events(events)

    source = LocalNDJSONEventSource(path)
    fetched = list(
        source.fetch_events(
            window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            window_end=datetime(2027, 1, 1, tzinfo=timezone.utc),
            tenant="tenant_acme",
        )
    )
    assert all(e.tenant_id == "tenant_acme" for e in fetched)
    assert len(fetched) == 2


# ---------------------------------------------------------------------------
# AWS isolation: boto3 must NOT be imported when opsmitra.event_source is loaded
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Step 7 fix-pass: M7 — malformed NDJSON line debug logging
# ---------------------------------------------------------------------------


def test_local_ndjson_source_logs_debug_on_malformed_line(tmp_path: Path, caplog):
    """A malformed NDJSON line must emit a DEBUG log with the line number and still yield valid events."""
    path = tmp_path / "events.jsonl"

    # Line 1: valid event; line 2: garbage; line 3: valid event
    valid_event = _make_event(0)
    valid_event2 = _make_event(1)
    lines = [
        json.dumps(valid_event.to_dict()),
        "this is not valid json {{{",
        json.dumps(valid_event2.to_dict()),
    ]
    path.write_text("\n".join(lines) + "\n")

    source = LocalNDJSONEventSource(path)
    with caplog.at_level(logging.DEBUG, logger="opsmitra.event_source"):
        fetched = list(
            source.fetch_events(
                window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                window_end=datetime(2027, 1, 1, tzinfo=timezone.utc),
            )
        )

    assert len(fetched) == 2
    debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    # Must have logged line 2 as malformed
    assert any("2" in msg for msg in debug_messages), f"No debug log for line 2: {debug_messages}"


def test_event_source_does_not_import_boto3():
    """Importing opsmitra.event_source must not cause boto3 to enter sys.modules.

    This test runs in a subprocess to guarantee a clean module state because
    pytest collection may have already imported boto3 indirectly.
    """
    code = (
        "import sys; "
        "import opsmitra.event_source; "
        "assert 'boto3' not in sys.modules, "
        "f'boto3 was imported by opsmitra.event_source: {list(sys.modules.keys())}'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=os.environ.copy(),  # N3: explicit env inheritance for reproducibility
    )
    assert result.returncode == 0, (
        f"boto3 isolation test failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
