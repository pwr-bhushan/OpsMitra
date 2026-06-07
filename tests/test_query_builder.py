"""RED tests for the pure SQL query builder (opsmitra.aws.query_builder).

All imports are expected to fail at collection time (RED state).
No boto3 is needed here — build_event_query is a pure string-producing function.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

# RED: this import fails until opsmitra/aws/query_builder.py is created
from opsmitra.aws.query_builder import build_event_query  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_TABLE = "opsmitra.events"


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Partition predicate shape — short windows (≤24 h)
# ---------------------------------------------------------------------------


def test_query_one_hour_window_has_exactly_one_partition_predicate():
    """A 1-hour window must produce exactly one (year=... AND month=... AND day=... AND hour=...) group."""
    sql = build_event_query(
        table=_BASE_TABLE,
        window_start=_utc(2026, 6, 6, 18),
        window_end=_utc(2026, 6, 6, 19),
    )
    # Exactly one hour predicate tuple
    assert sql.count("hour='18'") == 1
    assert sql.count("hour='") == 1
    assert "year='2026'" in sql
    assert "month='06'" in sql
    assert "day='06'" in sql


def test_query_two_hour_window_has_two_hour_predicates():
    """A 2-hour window must produce two OR-joined hour predicates."""
    sql = build_event_query(
        table=_BASE_TABLE,
        window_start=_utc(2026, 6, 6, 17),
        window_end=_utc(2026, 6, 6, 19),
    )
    assert "hour='17'" in sql
    assert "hour='18'" in sql
    # Both joined (OR is the natural combinator for partition predicates)
    assert " OR " in sql.upper() or sql.count("(year=") == 2


def test_query_two_hour_window_no_hour_19():
    """The window_end hour (exclusive) must NOT appear as a partition predicate."""
    sql = build_event_query(
        table=_BASE_TABLE,
        window_start=_utc(2026, 6, 6, 17),
        window_end=_utc(2026, 6, 6, 19),
    )
    # hour=19 is excluded (window_end is exclusive)
    assert "hour='19'" not in sql


# ---------------------------------------------------------------------------
# Multi-day window (>24 h) — day-granularity only
# ---------------------------------------------------------------------------


def test_query_multiday_window_has_day_predicates_not_hour():
    """Windows >24 h must enumerate (year, month, day) only — no hour= predicates.

    Plan decision (§5): 'Window > 24h: enumerate (year, month, day) triples;
    drop the hour predicate (full-day scan per partition).'
    """
    sql = build_event_query(
        table=_BASE_TABLE,
        window_start=_utc(2026, 6, 4, 0),
        window_end=_utc(2026, 6, 6, 12),  # ~60 hours > 24 h
    )
    # day predicates present
    assert "day='04'" in sql
    assert "day='05'" in sql
    # hour= must be absent from partition block
    assert "hour='" not in sql
    # Plan spec: SQL must contain a comment about day granularity
    assert "day granularity" in sql.lower() or "-- partition" in sql


def test_query_multiday_window_contains_timestamp_bounds():
    """Multi-day query must still bound rows with timestamp >= / < predicates."""
    sql = build_event_query(
        table=_BASE_TABLE,
        window_start=_utc(2026, 6, 4, 0),
        window_end=_utc(2026, 6, 6, 12),
    )
    assert "timestamp >=" in sql.lower() or "timestamp>=" in sql.lower()
    assert "timestamp <" in sql.lower() or "timestamp<" in sql.lower()


# ---------------------------------------------------------------------------
# Tenant filter
# ---------------------------------------------------------------------------


def test_query_tenant_filter_present():
    """With tenant='tenant_acme', SQL must contain both tenant and tenant_id predicates."""
    sql = build_event_query(
        table=_BASE_TABLE,
        window_start=_utc(2026, 6, 6, 18),
        window_end=_utc(2026, 6, 6, 19),
        tenant="tenant_acme",
    )
    # Partition-level predicate
    assert "tenant = 'tenant_acme'" in sql or "tenant='tenant_acme'" in sql
    # Row-level predicate (tenant_id column)
    assert "tenant_id = 'tenant_acme'" in sql or "tenant_id='tenant_acme'" in sql


def test_query_no_tenant_filter():
    """Without tenant, the SQL must not contain any 'tenant =' clause."""
    sql = build_event_query(
        table=_BASE_TABLE,
        window_start=_utc(2026, 6, 6, 18),
        window_end=_utc(2026, 6, 6, 19),
    )
    # Neither partition filter nor row filter should appear
    assert "tenant = '" not in sql
    assert "tenant_id = '" not in sql
    assert "tenant='" not in sql


# ---------------------------------------------------------------------------
# Types / endpoint filter
# ---------------------------------------------------------------------------


def test_query_type_filter_single_type():
    """A single type filter must produce endpoint IN ('/sms/send') in SQL."""
    sql = build_event_query(
        table=_BASE_TABLE,
        window_start=_utc(2026, 6, 6, 18),
        window_end=_utc(2026, 6, 6, 19),
        types=["/sms/send"],
    )
    assert "/sms/send" in sql
    assert "endpoint" in sql.lower()
    assert "IN" in sql.upper()


def test_query_type_filter_two_types():
    """Two types in the filter must produce both in the endpoint IN (...) clause."""
    sql = build_event_query(
        table=_BASE_TABLE,
        window_start=_utc(2026, 6, 6, 18),
        window_end=_utc(2026, 6, 6, 19),
        types=["/sms/send", "/auth/login"],
    )
    assert "/sms/send" in sql
    assert "/auth/login" in sql
    assert "IN" in sql.upper()


def test_query_no_type_filter():
    """Without types argument, endpoint IN clause must be absent."""
    sql = build_event_query(
        table=_BASE_TABLE,
        window_start=_utc(2026, 6, 6, 18),
        window_end=_utc(2026, 6, 6, 19),
    )
    # No endpoint restriction
    assert "endpoint IN" not in sql.upper()


# ---------------------------------------------------------------------------
# Injection safety
# ---------------------------------------------------------------------------


def test_query_rejects_sql_injection_in_tenant():
    """A tenant containing SQL metacharacters must raise ValueError.

    Plan §5: 're.fullmatch(r"[a-zA-Z0-9_\\-]+", tenant) MUST match, else raise
    ValueError("tenant contains unsafe characters")'
    """
    with pytest.raises(ValueError, match="unsafe"):
        build_event_query(
            table=_BASE_TABLE,
            window_start=_utc(2026, 6, 6, 18),
            window_end=_utc(2026, 6, 6, 19),
            tenant="x'; DROP TABLE events; --",
        )


def test_query_rejects_injection_tenant_no_drop_in_output():
    """Even if ValueError is not raised, DROP TABLE must not appear in SQL."""
    try:
        sql = build_event_query(
            table=_BASE_TABLE,
            window_start=_utc(2026, 6, 6, 18),
            window_end=_utc(2026, 6, 6, 19),
            tenant="x'; DROP TABLE events; --",
        )
        assert "DROP" not in sql.upper()
    except ValueError:
        pass  # preferred outcome


def test_query_rejects_invalid_endpoint_type():
    """A type not in the canonical endpoint set must raise ValueError.

    Plan §5: 'each value MUST appear in a whitelist … Reject otherwise with
    ValueError("type not in allowed set: <name>")'
    """
    with pytest.raises(ValueError, match="allowed"):
        build_event_query(
            table=_BASE_TABLE,
            window_start=_utc(2026, 6, 6, 18),
            window_end=_utc(2026, 6, 6, 19),
            types=["/evil/endpoint"],
        )


# ---------------------------------------------------------------------------
# General SQL sanity
# ---------------------------------------------------------------------------


def test_query_selects_from_correct_table():
    """Generated SQL must reference the table name passed in."""
    sql = build_event_query(
        table="opsmitra.events",
        window_start=_utc(2026, 6, 6, 18),
        window_end=_utc(2026, 6, 6, 19),
    )
    assert "opsmitra.events" in sql


def test_query_is_a_select_statement():
    """Generated SQL must be a SELECT statement."""
    sql = build_event_query(
        table=_BASE_TABLE,
        window_start=_utc(2026, 6, 6, 18),
        window_end=_utc(2026, 6, 6, 19),
    )
    assert sql.strip().upper().startswith("SELECT")


# ---------------------------------------------------------------------------
# Step 10 L3 (S7) — endpoint enum sourced from models.py
# ---------------------------------------------------------------------------


def test_endpoint_enum_sourced_from_models():
    """query_builder's allowed-endpoint set must be the same object (or equal set)
    as opsmitra.models.EVENT_TYPES, enforcing a single source of truth.

    RED until L3 (S7) extracts EVENT_TYPES to models.py and imports it in
    query_builder.py.
    """
    # RED: opsmitra.models.EVENT_TYPES does not exist yet → ImportError
    from opsmitra.models import EVENT_TYPES  # type: ignore[attr-defined]
    from opsmitra.aws import query_builder

    # After L3 fix: query_builder.EVENT_TYPES (or _ALLOWED_ENDPOINTS re-exported
    # as EVENT_TYPES) must be identical to (or a superset of) models.EVENT_TYPES.
    # Prefer checking identity (same object) first, then equality.
    qb_set = getattr(query_builder, "EVENT_TYPES", None) or getattr(
        query_builder, "_ALLOWED_ENDPOINTS", None
    )
    assert qb_set is not None, (
        "query_builder must expose EVENT_TYPES or _ALLOWED_ENDPOINTS"
    )
    assert qb_set == EVENT_TYPES, (
        f"query_builder endpoint set {qb_set!r} differs from models.EVENT_TYPES {EVENT_TYPES!r}"
    )
