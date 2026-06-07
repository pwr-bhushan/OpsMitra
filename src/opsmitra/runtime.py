"""OpsMitra runtime orchestrator: fetch events, detect anomalies, alert.

Composed via constructor injection so all collaborators are replaceable in
tests without any network or LLM I/O.

Structured logging rules (per Step 7 review + lessons.md):
- NEVER log raw Event objects (may contain user PII).
- NEVER log webhook URLs, AWS account IDs, Athena query strings, or S3 keys.
- NEVER log raw anomaly evidence dicts (may contain request_id, country, etc.).
- DO log counts, kinds, fingerprints, dry_run flag, and stage elapsed times.
"""

from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

from opsmitra.cooldown import AnomalyCooldown, fingerprint
from opsmitra.detectors import detect_anomalies
from opsmitra.event_source import EventSource
from opsmitra.models import Anomaly
from opsmitra.summarizer import AlertSummary, FallbackSummarizer
from opsmitra.config import DetectorThresholds

logger = logging.getLogger(__name__)

_REDACTED_EXC = "[exception_redacted]"


# ---------------------------------------------------------------------------
# Collaborator Protocols (structural typing — allows test fakes without subclassing)
# ---------------------------------------------------------------------------


@runtime_checkable
class _SummarizerProto(Protocol):
    def summarize(self, anomaly: Anomaly) -> AlertSummary: ...


@runtime_checkable
class _AlerterProto(Protocol):
    def send(self, summary: AlertSummary, anomaly: Anomaly) -> Any: ...


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class OpsMitraRuntimeError(RuntimeError):
    """Base class for all OpsMitra runtime errors."""


class RuntimeConfigurationError(OpsMitraRuntimeError):
    """Raised when the runtime is misconfigured (invalid window, missing URL, etc.)."""


class EventSourceError(OpsMitraRuntimeError):
    """Raised when the event source fails to fetch events."""


class SummarizerError(OpsMitraRuntimeError):
    """Raised when the summarizer fails and no fallback is available."""


class AlertDeliveryError(OpsMitraRuntimeError):
    """Raised when alert delivery fails terminally."""


# ---------------------------------------------------------------------------
# RuntimeResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeResult:
    """Immutable summary of a single Runtime.execute() call."""

    anomalies_detected: int
    anomalies_alerted: int
    anomalies_suppressed: int
    source_kind: str   # "local" | "athena"
    sink_kind: str     # "stdout" | "slack"
    dry_run: bool
    errors: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Structured logging helper
# ---------------------------------------------------------------------------


def _log_event(level: int, event_type: str, **fields: Any) -> None:
    """Emit a single ``key=value`` structured log line.

    NEVER call this with webhook URLs, raw event objects, or anomaly evidence.
    """
    parts = [f"event={event_type}"] + [f"{k}={v}" for k, v in fields.items()]
    logger.log(level, " ".join(parts))


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class Runtime:
    """Orchestrates one execution window: fetch → detect → alert → persist.

    All collaborators are injected via constructor so the class is fully
    testable without network or filesystem I/O.

    Args:
        source: EventSource that implements ``fetch_events``.
        summarizer: Object with ``summarize(anomaly) -> AlertSummary``.
        alerter: Object with ``send(summary, anomaly) -> DeliveryResult``.
        cooldown: ``AnomalyCooldown`` for suppression tracking.
        thresholds: ``DetectorThresholds``. Forwarded to ``detect_anomalies`` as
            ``thresholds=…``; the public ``DetectorThresholds`` knobs
            (``sms_abuse_rate_per_minute``, ``auth_burst_count``,
            ``endpoint_error_rate``, ``endpoint_error_min_samples``,
            ``cost_runaway_delta_usd``, ``*_window_seconds``) tune the
            absolute-threshold sub-detectors. The baseline-ratio defense (5×
            SMS, 10× cost) is preserved at ``DetectionConfig`` defaults and is
            NOT tunable via ``DetectorThresholds`` in v0.
        clock: Injectable callable returning UTC datetime (for testing).
        max_events_per_window: Hard cap on events fetched per window.
        dry_run: When True, alerter is expected to return delivered=True without
            network I/O. Surfaced in RuntimeResult.
    """

    def __init__(
        self,
        source: EventSource,
        summarizer: _SummarizerProto,
        alerter: _AlerterProto,
        cooldown: AnomalyCooldown,
        thresholds: DetectorThresholds,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_events_per_window: int = 1_000_000,
        dry_run: bool = True,
        source_kind: str = "local",
        sink_kind: str = "stdout",
        fallback_summarizer: _SummarizerProto | None = None,
    ) -> None:
        self._source = source
        self._summarizer = summarizer
        self._alerter = alerter
        self._cooldown = cooldown
        self._thresholds = thresholds
        self._clock = clock
        self._max_events = max_events_per_window
        self._dry_run = dry_run
        self._source_kind = source_kind
        self._sink_kind = sink_kind
        self._fallback = fallback_summarizer or FallbackSummarizer()

        _alerter_config = getattr(alerter, "_config", None) or getattr(alerter, "config", None)
        if not self._dry_run and _alerter_config is not None:
            if _alerter_config.slack_webhook_url is None:
                raise RuntimeConfigurationError(
                    "OPSMITRA_SLACK_WEBHOOK_URL is required when dry_run is false"
                )

    def execute(
        self,
        window_start: datetime,
        window_end: datetime,
        tenant: str | None = None,
        types: Sequence[str] | None = None,
    ) -> RuntimeResult:
        """Run one pipeline execution window.

        Args:
            window_start: Start of the detection window (tz-aware UTC).
            window_end: End of the detection window (tz-aware UTC).
            tenant: Optional tenant filter passed to the event source.
            types: Optional endpoint type filter passed to the event source.

        Returns:
            ``RuntimeResult`` with counts and error strings.

        Raises:
            RuntimeConfigurationError: If the window is invalid.
            EventSourceError: If event fetching fails (terminal — CLI exits 1).
        """
        # Stage 0 — validate window
        if window_end <= window_start:
            raise RuntimeConfigurationError(
                f"window_end must be after window_start: "
                f"{window_end.isoformat()} <= {window_start.isoformat()}"
            )

        _log_event(
            logging.INFO,
            "runtime_start",
            source_kind=self._source_kind,
            sink_kind=self._sink_kind,
            dry_run=self._dry_run,
            window_start_iso=window_start.isoformat(),
            window_end_iso=window_end.isoformat(),
            tenant=tenant or "all",
        )

        # Stage 1 — fetch events
        t0 = time.monotonic()
        try:
            raw_iter = self._source.fetch_events(
                window_start, window_end, tenant=tenant, types=types
            )
            # Fetch max+1 to detect truncation without loading unbounded data
            raw_events = list(itertools.islice(raw_iter, self._max_events + 1))
        except OpsMitraRuntimeError:
            raise
        except Exception as exc:
            raise EventSourceError(
                f"Event source failed: {type(exc).__name__}"
            ) from exc

        truncated = len(raw_events) > self._max_events
        if truncated:
            raw_events = raw_events[: self._max_events]
            _log_event(
                logging.WARNING,
                "events_truncated",
                limit=self._max_events,
            )

        elapsed_fetch_ms = int((time.monotonic() - t0) * 1000)
        _log_event(
            logging.INFO,
            "events_fetched",
            count=len(raw_events),
            elapsed_ms=elapsed_fetch_ms,
            truncated=truncated,
        )

        # Stage 2 — detect anomalies
        t1 = time.monotonic()
        # M6 (Step 10): pass DetectorThresholds through to detect_anomalies.
        anomalies: list[Anomaly] = detect_anomalies(
            raw_events, window_start, window_end, thresholds=self._thresholds
        )
        elapsed_detect_ms = int((time.monotonic() - t1) * 1000)
        _log_event(
            logging.INFO,
            "detection_complete",
            anomalies_detected=len(anomalies),
            elapsed_ms=elapsed_detect_ms,
        )

        # Stage 3 — per-anomaly loop
        alerted = 0
        suppressed = 0
        errors: list[str] = []

        for anomaly in anomalies:
            now = self._clock()
            fp = fingerprint(anomaly)

            if not self._cooldown.should_alert(anomaly, now):
                _log_event(
                    logging.INFO,
                    "anomaly_suppressed",
                    fingerprint=fp,
                )
                suppressed += 1
                continue

            # Summarize — on failure use FallbackSummarizer (never abort run)
            try:
                summary = self._summarizer.summarize(anomaly)
            except Exception as exc:
                _log_event(
                    logging.WARNING,
                    "summarizer_failed",
                    fingerprint=fp,
                    exc_type=type(exc).__name__,
                )
                summary = self._fallback.summarize(anomaly)

            # Alert delivery
            try:
                result = self._alerter.send(summary, anomaly)
            except Exception as exc:
                safe_msg = _safe_error_message(exc, self._alerter)
                errors.append(safe_msg)
                _log_event(
                    logging.ERROR,
                    "alert_failed",
                    fingerprint=fp,
                    exc_type=type(exc).__name__,
                )
                continue

            if result.delivered:
                self._cooldown.record_alerted(anomaly, now)
                alerted += 1
                _log_event(
                    logging.INFO,
                    "alert_sent",
                    fingerprint=fp,
                    dry_run=result.dry_run,
                    attempts=result.attempts,
                )
            else:
                err_msg = f"slack_send_failed fingerprint={fp}"
                errors.append(err_msg)
                _log_event(
                    logging.WARNING,
                    "alert_delivery_failed",
                    fingerprint=fp,
                )

        # Stage 4 — prune + persist
        self._cooldown.prune_expired(self._clock())
        self._cooldown.persist()

        runtime_result = RuntimeResult(
            anomalies_detected=len(anomalies),
            anomalies_alerted=alerted,
            anomalies_suppressed=suppressed,
            source_kind=self._source_kind,
            sink_kind=self._sink_kind,
            dry_run=self._dry_run,
            errors=tuple(errors),
        )

        _log_event(
            logging.INFO,
            "runtime_done",
            anomalies_detected=runtime_result.anomalies_detected,
            anomalies_alerted=runtime_result.anomalies_alerted,
            anomalies_suppressed=runtime_result.anomalies_suppressed,
            error_count=len(errors),
        )

        return runtime_result


# ---------------------------------------------------------------------------
# Error redaction helper
# ---------------------------------------------------------------------------


def _safe_error_message(exc: Exception, alerter: Any) -> str:
    """Return a redacted error string that does not contain any URLs.

    Uses the alerter's ``_redact`` method if available; otherwise returns
    only the exception type name (never the message, which may contain URLs).
    """
    if hasattr(alerter, "_redact"):
        try:
            return alerter._redact(str(exc))
        except Exception:
            pass
    return type(exc).__name__
