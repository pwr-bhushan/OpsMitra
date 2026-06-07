"""RED tests for AthenaEventSource using botocore.stub.Stubber.

All tests skip if botocore.stub is unavailable.
No real AWS credentials or network calls are made.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

botocore_stub = pytest.importorskip("botocore.stub")

import botocore.session  # noqa: E402

# RED: these imports fail until the module is implemented
from opsmitra.aws.athena_source import AthenaEventSource  # noqa: E402
from opsmitra.models import Event  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WINDOW_START = datetime(2026, 6, 6, 18, 0, 0, tzinfo=timezone.utc)
_WINDOW_END = datetime(2026, 6, 6, 19, 0, 0, tzinfo=timezone.utc)

_CONFIG = {
    "database": "opsmitra",
    "table": "events",
    "output_location": "s3://test-bucket/results/",
    "workgroup": "primary",
    "region": "us-east-1",
}

_QUERY_EXECUTION_ID = "exec-id-0001"


def _make_athena_client():
    """Return a botocore Athena client (not boto3) for use with Stubber."""
    session = botocore.session.get_session()
    return session.create_client("athena", region_name="us-east-1")


def _start_execution_response():
    return {"QueryExecutionId": _QUERY_EXECUTION_ID}


def _get_execution_response(state: str):
    return {
        "QueryExecution": {
            "QueryExecutionId": _QUERY_EXECUTION_ID,
            "Status": {"State": state},
        }
    }


def _get_results_response(rows: list[list[str]]):
    """Build a GetQueryResults response with a header row + data rows."""
    columns = ["timestamp", "tenant_id", "user_id", "api_key_id", "endpoint",
               "method", "status_code", "latency_ms", "ip", "country",
               "cost_units", "provider", "request_id"]
    result_rows = [
        {"Data": [{"VarCharValue": col} for col in columns]}
    ]
    for data_row in rows:
        result_rows.append(
            {"Data": [{"VarCharValue": v} for v in data_row]}
        )
    return {
        "ResultSet": {
            "Rows": result_rows,
            "ResultSetMetadata": {
                "ColumnInfo": [{"Name": col, "Type": "varchar"} for col in columns]
            },
        }
    }


def _sample_row(i: int) -> list[str]:
    return [
        f"2026-06-06T18:0{i}:00Z",   # timestamp
        "tenant_acme",               # tenant_id
        f"user_{i:03d}",             # user_id
        "key_acme_normal",           # api_key_id
        "/sms/send",                 # endpoint
        "POST",                      # method
        "200",                       # status_code
        "120",                       # latency_ms
        "1.2.3.4",                   # ip
        "US",                        # country
        "1.0",                       # cost_units
        "twilio",                    # provider
        f"req_{i:04d}",              # request_id
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_athena_source_returns_events_via_stubber():
    """Stubber: StartQueryExecution → GetQueryExecution(SUCCEEDED) → GetQueryResults(2 rows)
    must yield exactly 2 Event objects."""
    client = _make_athena_client()
    stubber = botocore_stub.Stubber(client)

    stubber.add_response(
        "start_query_execution",
        _start_execution_response(),
        expected_params=None,
    )
    stubber.add_response(
        "get_query_execution",
        _get_execution_response("SUCCEEDED"),
        expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID},
    )
    stubber.add_response(
        "get_query_results",
        _get_results_response([_sample_row(0), _sample_row(1)]),
        expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID},
    )

    with stubber:
        source = AthenaEventSource(client=client, **_CONFIG)
        events = list(source.fetch_events(window_start=_WINDOW_START, window_end=_WINDOW_END))

    assert len(events) == 2
    assert all(isinstance(e, Event) for e in events)
    assert events[0].request_id == "req_0000"
    assert events[1].request_id == "req_0001"


def test_athena_source_raises_on_failed_state():
    """When GetQueryExecution returns FAILED, AthenaEventSource must raise a clear error.

    The error must NOT include AWS credentials or sensitive config — it must
    mention the failure state so callers can diagnose without confusion.
    """
    client = _make_athena_client()
    stubber = botocore_stub.Stubber(client)

    stubber.add_response(
        "start_query_execution",
        _start_execution_response(),
        expected_params=None,
    )
    stubber.add_response(
        "get_query_execution",
        _get_execution_response("FAILED"),
        expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID},
    )

    with stubber:
        source = AthenaEventSource(client=client, **_CONFIG)
        with pytest.raises(Exception) as exc_info:
            list(source.fetch_events(window_start=_WINDOW_START, window_end=_WINDOW_END))

    msg = str(exc_info.value).lower()
    # The error must mention the failure state
    assert "fail" in msg or "failed" in msg
    # Must NOT leak credential-like strings (conservative check on the error message)
    assert "secret" not in msg
    assert "password" not in msg


def test_athena_source_polls_until_succeeded():
    """State RUNNING → RUNNING → SUCCEEDED must be handled with polling.

    time.sleep is mocked to avoid wall-clock delay in tests.
    """
    client = _make_athena_client()
    stubber = botocore_stub.Stubber(client)

    stubber.add_response("start_query_execution", _start_execution_response(), expected_params=None)
    stubber.add_response("get_query_execution", _get_execution_response("RUNNING"),
                         expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID})
    stubber.add_response("get_query_execution", _get_execution_response("RUNNING"),
                         expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID})
    stubber.add_response("get_query_execution", _get_execution_response("SUCCEEDED"),
                         expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID})
    stubber.add_response(
        "get_query_results",
        _get_results_response([_sample_row(0)]),
        expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID},
    )

    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    with stubber:
        with patch("time.sleep", fake_sleep):
            source = AthenaEventSource(client=client, **_CONFIG)
            events = list(source.fetch_events(window_start=_WINDOW_START, window_end=_WINDOW_END))

    assert len(events) == 1
    # Sleep must have been called at least twice (once per RUNNING state)
    assert len(sleep_calls) >= 2


def test_athena_source_tenant_filter_passed_to_query():
    """When tenant is specified, the generated query must include a tenant filter."""
    client = _make_athena_client()
    stubber = botocore_stub.Stubber(client)

    stubber.add_response("start_query_execution", _start_execution_response(), expected_params=None)
    stubber.add_response("get_query_execution", _get_execution_response("SUCCEEDED"),
                         expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID})
    stubber.add_response("get_query_results", _get_results_response([]),
                         expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID})

    with stubber:
        source = AthenaEventSource(client=client, **_CONFIG)
        list(source.fetch_events(
            window_start=_WINDOW_START,
            window_end=_WINDOW_END,
            tenant="tenant_acme",
        ))
    # We can't easily intercept the exact QueryString here via the Stubber alone,
    # but the call must complete without error when tenant is supplied.
    # The query_builder tests assert the SQL content separately.
    stubber.assert_no_pending_responses()


# ---------------------------------------------------------------------------
# Step 7 fix-pass: Phase 2 (M3) + Phase 3 (H1, H3) tests
# ---------------------------------------------------------------------------


def test_athena_failure_logs_warning_with_execution_id(caplog):
    """When Athena returns FAILED, a WARNING log must include the execution ID and state."""
    import logging

    client = _make_athena_client()
    stubber = botocore_stub.Stubber(client)

    stubber.add_response("start_query_execution", _start_execution_response(), expected_params=None)
    stubber.add_response(
        "get_query_execution",
        _get_execution_response("FAILED"),
        expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID},
    )

    with stubber:
        source = AthenaEventSource(client=client, **_CONFIG)
        with caplog.at_level(logging.WARNING, logger="opsmitra.aws.athena_source"):
            with pytest.raises(Exception):
                list(source.fetch_events(window_start=_WINDOW_START, window_end=_WINDOW_END))

    assert any(
        _QUERY_EXECUTION_ID in r.message and "FAILED" in r.message
        for r in caplog.records
    )


def test_athena_handles_null_header_cell():
    """Header row with a missing VarCharValue must produce an empty-string column name, not KeyError."""
    client = _make_athena_client()
    stubber = botocore_stub.Stubber(client)

    # Build a results response where the first header cell has no VarCharValue
    columns = ["timestamp", "tenant_id", "user_id", "api_key_id", "endpoint",
               "method", "status_code", "latency_ms", "ip", "country",
               "cost_units", "provider", "request_id"]
    header_row = {"Data": [{}] + [{"VarCharValue": col} for col in columns[1:]]}
    data_row = {"Data": [{"VarCharValue": v} for v in _sample_row(0)]}
    results_response = {
        "ResultSet": {
            "Rows": [header_row, data_row],
            "ResultSetMetadata": {
                "ColumnInfo": [{"Name": col, "Type": "varchar"} for col in columns],
            },
        }
    }

    stubber.add_response("start_query_execution", _start_execution_response(), expected_params=None)
    stubber.add_response(
        "get_query_execution",
        _get_execution_response("SUCCEEDED"),
        expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID},
    )
    stubber.add_response(
        "get_query_results",
        results_response,
        expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID},
    )

    with stubber:
        source = AthenaEventSource(client=client, **_CONFIG)
        # Must not raise KeyError — null header cell is tolerated
        events = list(source.fetch_events(window_start=_WINDOW_START, window_end=_WINDOW_END))

    # The row may or may not parse to a valid Event (column "" won't match timestamp),
    # but no exception is raised.
    assert isinstance(events, list)


def test_athena_polling_timeout_raises_informative_error():
    """Exhausting max_polls without terminal state must raise AthenaQueryError with 'polling exceeded'."""
    from opsmitra.aws.athena_source import AthenaQueryError

    client = _make_athena_client()
    stubber = botocore_stub.Stubber(client)

    stubber.add_response("start_query_execution", _start_execution_response(), expected_params=None)
    # Add 3 RUNNING responses for max_polls=3
    for _ in range(3):
        stubber.add_response(
            "get_query_execution",
            _get_execution_response("RUNNING"),
            expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID},
        )

    with stubber:
        source = AthenaEventSource(client=client, poll_interval=0.0, max_polls=3, **_CONFIG)
        with pytest.raises(AthenaQueryError, match="polling exceeded"):
            list(source.fetch_events(window_start=_WINDOW_START, window_end=_WINDOW_END))


def test_athena_custom_poll_interval_and_max_polls_honored():
    """poll_interval and max_polls constructor params are respected during polling."""
    from unittest.mock import MagicMock

    client = _make_athena_client()
    stubber = botocore_stub.Stubber(client)

    # 6 RUNNING states then SUCCEEDED — with max_polls=7 this should succeed
    stubber.add_response("start_query_execution", _start_execution_response(), expected_params=None)
    for _ in range(6):
        stubber.add_response(
            "get_query_execution",
            _get_execution_response("RUNNING"),
            expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID},
        )
    stubber.add_response(
        "get_query_execution",
        _get_execution_response("SUCCEEDED"),
        expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID},
    )
    stubber.add_response(
        "get_query_results",
        _get_results_response([]),
        expected_params={"QueryExecutionId": _QUERY_EXECUTION_ID},
    )

    sleep_mock = MagicMock()

    with stubber:
        source = AthenaEventSource(
            client=client, poll_interval=0.5, max_polls=7, sleep=sleep_mock, **_CONFIG
        )
        list(source.fetch_events(window_start=_WINDOW_START, window_end=_WINDOW_END))

    # sleep should have been called 6 times (once per RUNNING) with 0.5 seconds
    assert sleep_mock.call_count == 6
    sleep_mock.assert_called_with(0.5)
