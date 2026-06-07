"""S3NDJSONEventSink — writes events to S3 as gzipped NDJSON partitions.

boto3 is imported here. This module must NOT be imported in non-AWS paths.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

import boto3

logger = logging.getLogger(__name__)

_MAX_GZIP_BYTES = 100_000_000  # 100 MB hard cap — hourly partitions are far below this
_GZIP_WARN_BYTES = _MAX_GZIP_BYTES // 2  # 50 MB soft warning threshold

from opsmitra.event_source import WriteResult
from opsmitra.models import Event


class S3NDJSONEventSink:
    """Writes events to S3 as partitioned gzipped NDJSON files.

    Partition layout:
        {prefix}/tenant={t}/year={YYYY}/month={MM}/day={DD}/hour={HH}/events.jsonl.gz

    Accepts an injectable boto3/botocore client so tests can stub without
    touching the network.
    """

    def __init__(
        self,
        bucket: str | None,
        prefix: str = "opsmitra-events",
        region: str = "us-east-1",
        client: Any | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("S3 bucket is required")
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")
        self._region = region
        self._client = client or boto3.client("s3", region_name=region)

    def write_events(self, events: Iterable[Event]) -> WriteResult:
        """Group events by (tenant, year, month, day, hour) and upload each group.

        Args:
            events: Events to write.

        Returns:
            WriteResult with total count written and list of partition key strings.
        """
        # Group events by partition tuple
        partitions: dict[tuple[str, str, str, str, str], list[Event]] = defaultdict(list)
        for event in events:
            key = _partition_key(event)
            partitions[key].append(event)

        if not partitions:
            return WriteResult(written=0, partitions=[])

        batch_id = uuid.uuid4().hex[:12]
        total_written = 0
        partition_strings: list[str] = []

        for (tenant, year, month, day, hour), group in sorted(partitions.items()):
            body = _build_gzip_ndjson(group)
            s3_key = (
                f"{self._prefix}"
                f"/tenant={tenant}"
                f"/year={year}"
                f"/month={month}"
                f"/day={day}"
                f"/hour={hour}"
                f"/events-{batch_id}.jsonl.gz"
            )
            self._client.put_object(
                Bucket=self._bucket,
                Key=s3_key,
                Body=body,
                ContentEncoding="gzip",
                ContentType="application/x-ndjson",
            )
            partition_str = (
                f"tenant={tenant}/year={year}/month={month}/day={day}/hour={hour}"
            )
            partition_strings.append(partition_str)
            total_written += len(group)

        return WriteResult(written=total_written, partitions=partition_strings)


def _partition_key(event: Event) -> tuple[str, str, str, str, str]:
    """Derive the (tenant, year, month, day, hour) partition key from an event."""
    ts: datetime = event.timestamp.astimezone(timezone.utc)
    return (
        event.tenant_id,
        str(ts.year),
        f"{ts.month:02d}",
        f"{ts.day:02d}",
        f"{ts.hour:02d}",
    )


def _build_gzip_ndjson(events: list[Event]) -> bytes:
    """Serialize events to gzip-compressed NDJSON bytes.

    Raises:
        ValueError: If the compressed body exceeds _MAX_GZIP_BYTES.
    """
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for event in events:
            line = json.dumps(event.to_dict()) + "\n"
            gz.write(line.encode("utf-8"))
    body = buf.getvalue()
    size = len(body)
    if size > _MAX_GZIP_BYTES:
        raise ValueError("S3 partition body exceeds %d bytes" % _MAX_GZIP_BYTES)
    if size > _GZIP_WARN_BYTES:
        logger.warning(
            "S3 partition body is %d bytes (warn threshold %d)", size, _GZIP_WARN_BYTES
        )
    return body
