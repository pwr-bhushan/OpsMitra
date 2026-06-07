"""Pure SQL query builder for Athena event queries.

This module has NO boto3 imports — it only produces SQL strings.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Sequence

# L3 (S7): Import canonical endpoint set from models — single source of truth.
from opsmitra.models import EVENT_TYPES

# Re-export under the legacy name for backward compatibility.
_ALLOWED_ENDPOINTS: frozenset[str] = EVENT_TYPES

# Regex for safe tenant identifiers.
_TENANT_RE = re.compile(r"[a-zA-Z0-9_\-]+")


def build_event_query(
    table: str,
    window_start: datetime,
    window_end: datetime,
    tenant: str | None = None,
    types: Sequence[str] | None = None,
) -> str:
    """Build a partition-aware Athena SELECT query for the events table.

    Args:
        table: Fully-qualified table name, e.g. "opsmitra.events".
        window_start: Start of the time window (UTC, inclusive).
        window_end: End of the time window (UTC, exclusive).
        tenant: Optional tenant ID filter. Must match [a-zA-Z0-9_-]+.
        types: Optional list of endpoint strings to filter on. Must be in
            the allowed endpoint whitelist.

    Returns:
        A multi-line SQL SELECT statement as a string.

    Raises:
        ValueError: If tenant contains unsafe characters or a type is not
            in the allowed endpoint set.
    """
    # --- Input validation ---
    if tenant is not None:
        if not _TENANT_RE.fullmatch(tenant):
            raise ValueError(
                f"tenant contains unsafe characters: {tenant!r}"
            )

    if types is not None:
        for t in types:
            if t not in _ALLOWED_ENDPOINTS:
                raise ValueError(f"type not in allowed set: {t!r}")

    # Ensure datetimes are UTC
    start_utc = window_start.astimezone(timezone.utc)
    end_utc = window_end.astimezone(timezone.utc)

    # --- Partition predicate ---
    window_hours = (end_utc - start_utc).total_seconds() / 3600.0
    multiday = window_hours > 24.0

    partition_clause = _build_partition_clause(start_utc, end_utc, multiday)

    # --- WHERE clause ---
    where_parts: list[str] = [partition_clause]

    # Timestamp bounds
    start_iso = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    where_parts.append(f"timestamp >= '{start_iso}'")
    where_parts.append(f"timestamp < '{end_iso}'")

    # Tenant filter (both partition column and data column)
    if tenant is not None:
        where_parts.append(f"tenant = '{tenant}'")
        where_parts.append(f"tenant_id = '{tenant}'")

    # Endpoint / type filter
    if types is not None and len(types) > 0:
        in_list = ", ".join(f"'{t}'" for t in sorted(set(types)))
        where_parts.append(f"endpoint IN ({in_list})")

    where_sql = "\n  AND ".join(where_parts)

    sql = f"SELECT *\nFROM {table}\nWHERE {where_sql}"
    return sql


def _build_partition_clause(
    start_utc: datetime, end_utc: datetime, multiday: bool
) -> str:
    """Build the partition predicate block."""
    if multiday:
        # -- partition pruning at day granularity
        day_tuples = _enumerate_days(start_utc, end_utc)
        predicates = [
            f"(year='{y}' AND month='{m:02d}' AND day='{d:02d}')"
            for y, m, d in day_tuples
        ]
        comment = "-- partition pruning at day granularity"
        joined = "\n  OR ".join(predicates)
        return f"{comment}\n({joined})"
    else:
        hour_tuples = _enumerate_hours(start_utc, end_utc)
        predicates = [
            f"(year='{y}' AND month='{m:02d}' AND day='{d:02d}' AND hour='{h:02d}')"
            for y, m, d, h in hour_tuples
        ]
        joined = "\n  OR ".join(predicates)
        return f"({joined})"


def _enumerate_hours(
    start: datetime, end: datetime
) -> list[tuple[str, int, int, int]]:
    """Enumerate (year, month, day, hour) tuples covering [start, end) by hour."""
    result: list[tuple[str, int, int, int]] = []
    current = start.replace(minute=0, second=0, microsecond=0)
    while current < end:
        result.append((str(current.year), current.month, current.day, current.hour))
        current = current + timedelta(hours=1)
    return result


def _enumerate_days(
    start: datetime, end: datetime
) -> list[tuple[str, int, int]]:
    """Enumerate (year, month, day) tuples covering [start, end) by day."""
    result: list[tuple[str, int, int]] = []
    current = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while current < end:
        result.append((str(current.year), current.month, current.day))
        current = current + timedelta(days=1)
    return result
