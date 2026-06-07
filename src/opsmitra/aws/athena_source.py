"""AthenaEventSource — reads events from Athena via boto3.

boto3 is imported here. This module must NOT be imported in non-AWS paths.
"""

from __future__ import annotations

# Import time under an alias so tests that patch("opsmitra.aws.athena_source._time_module.sleep")
# observe the patched reference while production code uses the real module.
import logging
import time as _time_module
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Callable, Iterable

import boto3

logger = logging.getLogger(__name__)

from opsmitra.aws.query_builder import build_event_query
from opsmitra.models import Event


class AthenaQueryError(RuntimeError):
    """Raised when an Athena query finishes in a failed or cancelled state."""


class AthenaEventSource:
    """Fetches events from Athena by issuing a StartQueryExecution and polling.

    Accepts an injectable boto3/botocore client and sleep callable so tests
    can stub both without touching the network.
    """

    # Non-terminal states that keep the poll loop running: QUEUED, RUNNING.
    _TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})

    def __init__(
        self,
        database: str,
        table: str,
        output_location: str,
        workgroup: str = "primary",
        region: str = "us-east-1",
        client: Any | None = None,
        sleep: Callable[[float], None] | None = None,
        query_builder: Callable[..., str] = build_event_query,
        poll_interval: float = 2.0,
        max_polls: int = 300,
    ) -> None:
        self._database = database
        self._table = table
        self._output_location = output_location
        self._workgroup = workgroup
        self._region = region
        self._client = client or boto3.client("athena", region_name=region)
        # Store None so that at call time we call time.sleep (respecting monkeypatching).
        # Explicit override (e.g. in tests) is still supported.
        self._sleep = sleep
        self._query_builder = query_builder
        self._poll_interval = poll_interval
        self._max_polls = max_polls

    def fetch_events(
        self,
        window_start: datetime,
        window_end: datetime,
        tenant: str | None = None,
        types: Sequence[str] | None = None,
    ) -> Iterable[Event]:
        """Execute an Athena query and yield Event objects from the results.

        Args:
            window_start: Inclusive window start (UTC).
            window_end: Exclusive window end (UTC).
            tenant: Optional tenant ID filter.
            types: Optional endpoint whitelist.

        Yields:
            Event objects from the query result set.

        Raises:
            AthenaQueryError: If the query ends in FAILED or CANCELLED state.
        """
        sql = self._query_builder(
            table=self._table,
            window_start=window_start,
            window_end=window_end,
            tenant=tenant,
            types=types,
        )

        # Start the query
        response = self._client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": self._database},
            WorkGroup=self._workgroup,
            ResultConfiguration={"OutputLocation": self._output_location},
        )
        execution_id: str = response["QueryExecutionId"]

        # Poll until terminal
        state = self._poll_until_done(execution_id)

        if state in ("FAILED", "CANCELLED"):
            logger.warning("athena query %s ended in state %s", execution_id, state)
            raise AthenaQueryError(f"Athena query failed with state: {state}")

        # Fetch results (single-page; pagination is out of scope per plan §11)
        results = self._client.get_query_results(QueryExecutionId=execution_id)
        rows = results["ResultSet"]["Rows"]
        if not rows:
            return

        # First row is the header — use .get() to tolerate NULL/missing cells
        columns = [col.get("VarCharValue", "") for col in rows[0]["Data"]]

        for row in rows[1:]:
            cells = [cell.get("VarCharValue", "") for cell in row["Data"]]
            event = _row_to_event(cells, columns)
            if event is not None:
                yield event

    def _poll_until_done(self, execution_id: str) -> str:
        for _ in range(self._max_polls):
            response = self._client.get_query_execution(
                QueryExecutionId=execution_id
            )
            state: str = response["QueryExecution"]["Status"]["State"]
            if state in self._TERMINAL_STATES:
                return state
            sleep_fn = self._sleep if self._sleep is not None else _time_module.sleep
            sleep_fn(self._poll_interval)
        raise AthenaQueryError(
            "Athena query polling exceeded %d attempts; query %s may still be running"
            % (self._max_polls, execution_id)
        )


def _row_to_event(cells: list[str], columns: list[str]) -> Event | None:
    """Convert a flat row from GetQueryResults into an Event.

    Returns None if the row cannot be parsed (silently skipped).
    """
    row_dict = dict(zip(columns, cells))
    try:
        return Event.from_dict(row_dict)
    except (ValueError, KeyError):
        return None
