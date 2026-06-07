"""EventSource and EventSink Protocols plus local NDJSON implementations.

This module intentionally has NO boto3 imports. AWS implementations live
exclusively under opsmitra.aws.* and are only imported when needed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol, Sequence, runtime_checkable

logger = logging.getLogger(__name__)

from opsmitra.models import Event


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteResult:
    """Result returned by EventSink.write_events."""

    written: int
    partitions: list[str]


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class EventSource(Protocol):
    """Protocol for reading events from any backend."""

    def fetch_events(
        self,
        window_start: datetime,
        window_end: datetime,
        tenant: str | None = None,
        types: Sequence[str] | None = None,
    ) -> Iterable[Event]:
        """Yield events in [window_start, window_end), optionally filtered by tenant and types."""
        ...


@runtime_checkable
class EventSink(Protocol):
    """Protocol for writing events to any backend."""

    def write_events(self, events: Iterable[Event]) -> WriteResult:
        """Persist events and return a WriteResult; implementations must be idempotent on retry."""
        ...


# ---------------------------------------------------------------------------
# Local implementations
# ---------------------------------------------------------------------------


class LocalNDJSONEventSource:
    """Reads events from a newline-delimited JSON file.

    Gracefully returns an empty iterable when the file does not exist.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def fetch_events(
        self,
        window_start: datetime,
        window_end: datetime,
        tenant: str | None = None,
        types: Sequence[str] | None = None,
    ) -> Iterable[Event]:
        """Yield events within [window_start, window_end) filtered by tenant/types."""
        if not self._path.exists():
            return

        with self._path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    event = Event.from_dict(data)
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    logger.debug("skipping malformed NDJSON line %d: %s", lineno, exc)
                    continue

                if not (window_start <= event.timestamp < window_end):
                    continue
                if tenant is not None and event.tenant_id != tenant:
                    continue
                if types is not None and event.endpoint not in types:
                    continue

                yield event


class LocalNDJSONEventSink:
    """Appends events to a newline-delimited JSON file.

    Creates the file if it does not exist; appends if it does.

    N2 (timestamp lex-order note): timestamps are serialised by Event.to_dict()
    as ISO-8601 with a ``Z`` suffix (e.g. "2026-06-07T10:00:00Z"). This format
    is lex-sortable and chrono-sortable in the same order, so sorting lines by
    timestamp string is equivalent to chronological sort. Partition iteration in
    AthenaEventSource / query_builder.py relies on the same property.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def write_events(self, events: Iterable[Event]) -> WriteResult:
        """Serialize events to NDJSON and append to the file.

        Returns:
            WriteResult with the count of events written and partitions=['local'].
        """
        count = 0
        lines: list[str] = []
        for event in events:
            lines.append(json.dumps(event.to_dict()))
            count += 1

        if lines:
            mode = "a" if self._path.exists() else "w"
            with self._path.open(mode, encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")

        return WriteResult(written=count, partitions=["local"])
