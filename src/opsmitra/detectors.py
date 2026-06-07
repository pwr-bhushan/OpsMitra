"""Deterministic anomaly detectors."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Iterable

from opsmitra.config import DetectorThresholds
from opsmitra.models import Anomaly, Event


@dataclass(frozen=True)
class DetectionConfig:
    sms_min_count: int = 500
    sms_ratio_threshold: float = 5.0
    auth_min_failures: int = 100
    auth_min_users: int = 20
    # M5: window seconds for burst/runaway sub-window evaluation.
    # When > 0, detectors only count events within a rolling sub-window of this width.
    auth_burst_window_seconds: int = 0  # 0 means: use full detection window (legacy behaviour)
    error_min_requests: int = 50
    error_rate_threshold: float = 0.35
    cost_min_units: float = 1_000.0
    cost_ratio_threshold: float = 10.0
    cost_runaway_window_seconds: int = 0  # 0 means: use full detection window (legacy behaviour)


def _config_from_thresholds(
    thresholds: "DetectorThresholds",
    window_start: datetime,
    window_end: datetime,
) -> "DetectionConfig":
    """Derive a DetectionConfig from DetectorThresholds.

    Field mapping (plan §6.1):
      sms_abuse_rate_per_minute  → sms_min_count (rate × window_minutes, ≥1)
      auth_burst_count           → auth_min_failures
      auth_burst_window_seconds  → auth_burst_window_seconds
      endpoint_error_rate        → error_rate_threshold
      endpoint_error_min_samples → error_min_requests
      cost_runaway_delta_usd     → cost_min_units
      cost_runaway_window_seconds → cost_runaway_window_seconds
    """
    window_minutes = max((window_end - window_start).total_seconds() / 60.0, 1.0)
    sms_min_count = max(1, int(thresholds.sms_abuse_rate_per_minute * window_minutes))
    return DetectionConfig(
        sms_min_count=sms_min_count,
        # sms_ratio_threshold intentionally NOT overridden — DetectionConfig default (5.0)
        # preserves the baseline-ratio defense. DetectorThresholds does NOT tune ratios in v0.
        auth_min_failures=thresholds.auth_burst_count,
        auth_min_users=1,
        auth_burst_window_seconds=thresholds.auth_burst_window_seconds,
        error_min_requests=thresholds.endpoint_error_min_samples,
        error_rate_threshold=thresholds.endpoint_error_rate,
        cost_min_units=thresholds.cost_runaway_delta_usd,
        # cost_ratio_threshold intentionally NOT overridden — DetectionConfig default (10.0)
        # preserves the baseline-ratio defense. DetectorThresholds does NOT tune ratios in v0.
        cost_runaway_window_seconds=thresholds.cost_runaway_window_seconds,
    )


def detect_anomalies(
    events: Iterable[Event],
    window_start: datetime,
    window_end: datetime,
    config: DetectionConfig | None = None,
    thresholds: "DetectorThresholds | None" = None,
) -> list[Anomaly]:
    # M6: when thresholds provided, derive a DetectionConfig from them.
    # thresholds takes precedence over config (thresholds is the public knob).
    if thresholds is not None:
        cfg = _config_from_thresholds(thresholds, window_start, window_end)
    else:
        cfg = config or DetectionConfig()
    ordered = sorted(events, key=lambda event: event.timestamp)
    baseline = [event for event in ordered if event.timestamp < window_start]
    current = [event for event in ordered if window_start <= event.timestamp < window_end]

    anomalies: list[Anomaly] = []
    anomalies.extend(_detect_sms_abuse(current, baseline, window_start, window_end, cfg))
    anomalies.extend(_detect_auth_failure_burst(current, window_start, window_end, cfg))
    anomalies.extend(_detect_error_rate_spike(current, baseline, window_start, window_end, cfg))
    anomalies.extend(_detect_cost_runaway(current, baseline, window_start, window_end, cfg))
    return anomalies


def _detect_sms_abuse(
    current: list[Event],
    baseline: list[Event],
    window_start: datetime,
    window_end: datetime,
    config: DetectionConfig,
) -> list[Anomaly]:
    groups: dict[tuple[str, str | None], list[Event]] = defaultdict(list)
    for event in current:
        if event.endpoint == "/sms/send":
            groups[(event.tenant_id, event.api_key_id)].append(event)

    anomalies: list[Anomaly] = []
    for (tenant_id, api_key_id), grouped in groups.items():
        observed_count = len(grouped)
        baseline_count = _count_baseline(baseline, lambda event: event.tenant_id == tenant_id and event.endpoint == "/sms/send")
        ratio = _ratio(observed_count, baseline_count)
        if observed_count >= config.sms_min_count and ratio >= config.sms_ratio_threshold:
            anomalies.append(
                Anomaly(
                    id=_anomaly_id("sms_abuse", window_start, tenant_id),
                    type="sms_abuse_spike",
                    severity="high",
                    window_start=window_start,
                    window_end=window_end,
                    tenant_id=tenant_id,
                    subject={"api_key_id": api_key_id, "endpoint": "/sms/send", "provider": "twilio"},
                    observed={"count": observed_count, "cost_units": sum(event.cost_units for event in grouped)},
                    baseline={"hourly_count": baseline_count},
                    ratio=ratio,
                    evidence=_evidence(grouped, note="single tenant/API key generated unusual SMS volume"),
                    recommended_actions=[
                        "Temporarily throttle the API key",
                        "Inspect destination numbers",
                        "Notify tenant owner",
                    ],
                )
            )
    return anomalies


def _detect_auth_failure_burst(
    current: list[Event],
    window_start: datetime,
    window_end: datetime,
    config: DetectionConfig,
) -> list[Anomaly]:
    groups: dict[str, list[Event]] = defaultdict(list)
    for event in current:
        if event.endpoint == "/auth/login" and event.status_code in {401, 403} and event.ip:
            groups[event.ip].append(event)

    anomalies: list[Anomaly] = []
    for ip, grouped in groups.items():
        # M5: when auth_burst_window_seconds > 0, find the maximum burst count
        # within any rolling sub-window of that width. Only events in the peak
        # sub-window contribute to the anomaly assessment.
        if config.auth_burst_window_seconds > 0:
            sorted_group = sorted(grouped, key=lambda e: e.timestamp)
            peak_events = _peak_burst_window(sorted_group, config.auth_burst_window_seconds)
        else:
            peak_events = grouped

        failure_count = len(peak_events)
        distinct_users = len({event.user_id for event in peak_events if event.user_id})
        if failure_count >= config.auth_min_failures and distinct_users >= config.auth_min_users:
            tenant_id = _dominant_value(event.tenant_id for event in peak_events)
            anomalies.append(
                Anomaly(
                    id=_anomaly_id("auth_failure", window_start, ip.replace(".", "_")),
                    type="auth_failure_burst",
                    severity="high",
                    window_start=window_start,
                    window_end=window_end,
                    tenant_id=tenant_id,
                    subject={"ip": ip, "endpoint": "/auth/login"},
                    observed={"failure_count": failure_count, "distinct_users": distinct_users},
                    baseline=None,
                    ratio=None,
                    evidence=_evidence(peak_events, note="one IP failed authentication across many users"),
                    recommended_actions=[
                        "Rate-limit or block the IP",
                        "Check credential stuffing signals",
                        "Review affected tenant login activity",
                    ],
                )
            )
    return anomalies


def _detect_error_rate_spike(
    current: list[Event],
    baseline: list[Event],
    window_start: datetime,
    window_end: datetime,
    config: DetectionConfig,
) -> list[Anomaly]:
    groups: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in current:
        groups[(event.tenant_id, event.endpoint)].append(event)

    anomalies: list[Anomaly] = []
    for (tenant_id, endpoint), grouped in groups.items():
        request_count = len(grouped)
        error_count = sum(1 for event in grouped if event.status_code >= 500)
        error_rate = error_count / request_count if request_count else 0.0
        baseline_rate = _baseline_error_rate(baseline, tenant_id, endpoint)
        if request_count >= config.error_min_requests and error_rate >= config.error_rate_threshold:
            anomalies.append(
                Anomaly(
                    id=_anomaly_id("error_rate", window_start, endpoint.strip("/").replace("/", "_") or "root"),
                    type="endpoint_error_rate_spike",
                    severity="high" if error_rate >= 0.5 else "medium",
                    window_start=window_start,
                    window_end=window_end,
                    tenant_id=tenant_id,
                    subject={"endpoint": endpoint},
                    observed={"request_count": request_count, "error_count": error_count, "error_rate": round(error_rate, 4)},
                    baseline={"error_rate": round(baseline_rate, 4)},
                    ratio=_ratio(error_rate, baseline_rate),
                    evidence=_evidence([event for event in grouped if event.status_code >= 500], note="endpoint returned elevated 5xx responses"),
                    recommended_actions=[
                        "Inspect recent deploys",
                        "Check dependency health",
                        "Review error logs for the endpoint",
                    ],
                )
            )
    return anomalies


def _detect_cost_runaway(
    current: list[Event],
    baseline: list[Event],
    window_start: datetime,
    window_end: datetime,
    config: DetectionConfig,
) -> list[Anomaly]:
    groups: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in current:
        if event.cost_units > 0:
            groups[(event.tenant_id, event.endpoint)].append(event)

    anomalies: list[Anomaly] = []
    for (tenant_id, endpoint), grouped in groups.items():
        # M5: when cost_runaway_window_seconds > 0, find the peak accumulated cost
        # within any rolling sub-window. Only events in that peak window are assessed.
        # A "runaway" requires at least 2 events clustering within the sub-window
        # (a single expensive event is normal; a burst is anomalous).
        if config.cost_runaway_window_seconds > 0:
            sorted_group = sorted(grouped, key=lambda e: e.timestamp)
            peak_events = _peak_cost_window(sorted_group, config.cost_runaway_window_seconds)
            # No burst if peak window contains fewer than 2 events
            if len(peak_events) < 2:
                continue
        else:
            peak_events = grouped

        observed_cost = sum(event.cost_units for event in peak_events)
        baseline_cost = sum(event.cost_units for event in baseline if event.tenant_id == tenant_id and event.endpoint == endpoint)
        ratio = _ratio(observed_cost, baseline_cost)
        if observed_cost >= config.cost_min_units and ratio >= config.cost_ratio_threshold:
            anomalies.append(
                Anomaly(
                    id=_anomaly_id("cost_runaway", window_start, f"{tenant_id}_{endpoint.strip('/').replace('/', '_')}"),
                    type="cost_runaway_usage",
                    severity="high",
                    window_start=window_start,
                    window_end=window_end,
                    tenant_id=tenant_id,
                    subject={"endpoint": endpoint, "provider": _dominant_value(event.provider or "unknown" for event in peak_events)},
                    observed={"event_count": len(peak_events), "cost_units": observed_cost},
                    baseline={"cost_units": baseline_cost},
                    ratio=ratio,
                    evidence=_evidence(peak_events, note="tenant workload consumed unusual cost units"),
                    recommended_actions=[
                        "Pause or throttle the workload",
                        "Inspect job retry loops",
                        "Set temporary spend guardrails",
                    ],
                )
            )
    return anomalies


# ---------------------------------------------------------------------------
# Public detector functions (Phase A — accept DetectorThresholds)
# ---------------------------------------------------------------------------

"""Public detector wrappers — Step 7.

These wrappers (``detect_sms_abuse``, ``detect_auth_failure_burst``,
``detect_error_rate_spike``, ``detect_cost_runaway``) use the Step 7
``DetectorThresholds`` config dataclass. They are intentionally separate from
the internal ``DetectionConfig``-driven ``detect_anomalies`` orchestrator and
use a DIFFERENT sensitivity profile (notably, the wrappers bypass the
baseline-ratio defense used by ``detect_anomalies``).

DO NOT mix wrapper calls with ``detect_anomalies`` on the same window.
Step 10 hardening will unify these two paths.
"""


def detect_sms_abuse(
    current: list[Event],
    baseline: list[Event],
    window_start: datetime,
    window_end: datetime,
    thresholds: DetectorThresholds | None = None,
) -> list[Anomaly]:
    """Public wrapper for SMS abuse detection using DetectorThresholds.

    Uses thresholds.sms_abuse_rate_per_minute as a per-minute rate threshold.
    Delegates to internal logic with the threshold mapped to DetectionConfig.
    """
    thresholds = thresholds or DetectorThresholds()
    # Derive sms_min_count from rate_per_minute × window_minutes
    window_minutes = max(
        (window_end - window_start).total_seconds() / 60.0, 1.0
    )
    sms_min_count = int(thresholds.sms_abuse_rate_per_minute * window_minutes)
    cfg = DetectionConfig(sms_min_count=max(1, sms_min_count), sms_ratio_threshold=1.0)
    return _detect_sms_abuse(current, baseline, window_start, window_end, cfg)


def detect_auth_failure_burst(
    current: list[Event],
    window_start: datetime,
    window_end: datetime,
    thresholds: DetectorThresholds | None = None,
) -> list[Anomaly]:
    """Public wrapper for auth failure burst detection using DetectorThresholds."""
    thresholds = thresholds or DetectorThresholds()
    cfg = DetectionConfig(
        auth_min_failures=thresholds.auth_burst_count,
        auth_min_users=1,
    )
    return _detect_auth_failure_burst(current, window_start, window_end, cfg)


def detect_error_rate_spike(
    current: list[Event],
    baseline: list[Event],
    window_start: datetime,
    window_end: datetime,
    thresholds: DetectorThresholds | None = None,
) -> list[Anomaly]:
    """Public wrapper for error rate spike detection using DetectorThresholds."""
    thresholds = thresholds or DetectorThresholds()
    cfg = DetectionConfig(
        error_min_requests=thresholds.endpoint_error_min_samples,
        error_rate_threshold=thresholds.endpoint_error_rate,
    )
    return _detect_error_rate_spike(current, baseline, window_start, window_end, cfg)


def detect_cost_runaway(
    current: list[Event],
    baseline: list[Event],
    window_start: datetime,
    window_end: datetime,
    thresholds: DetectorThresholds | None = None,
) -> list[Anomaly]:
    """Public wrapper for cost runaway detection using DetectorThresholds."""
    thresholds = thresholds or DetectorThresholds()
    cfg = DetectionConfig(
        cost_min_units=thresholds.cost_runaway_delta_usd,
        cost_ratio_threshold=1.0,
    )
    return _detect_cost_runaway(current, baseline, window_start, window_end, cfg)


def _peak_burst_window(sorted_events: list[Event], window_seconds: int) -> list[Event]:
    """Return the longest run of events that all fall within window_seconds of each other.

    Uses a two-pointer sliding window over the sorted event list. The returned
    list is the largest *count* of consecutive events whose timestamp span is
    <= window_seconds. (Not the "densest" slice — peak-density would require a
    different algorithm; the existing test pins the longest-run semantic.)
    """
    if not sorted_events:
        return []
    best: list[Event] = []
    left = 0
    for right in range(len(sorted_events)):
        # Shrink window from left until all events span <= window_seconds
        while (
            sorted_events[right].timestamp - sorted_events[left].timestamp
        ).total_seconds() > window_seconds:
            left += 1
        if (right - left + 1) > len(best):
            best = sorted_events[left : right + 1]
    return best


def _peak_cost_window(sorted_events: list[Event], window_seconds: int) -> list[Event]:
    """Return the subset of events within the costliest consecutive window_seconds slice.

    Uses a two-pointer sliding window; selects the window with the highest total cost.
    """
    if not sorted_events:
        return []
    best: list[Event] = []
    best_cost: float = 0.0
    left = 0
    window_cost = 0.0
    for right in range(len(sorted_events)):
        window_cost += sorted_events[right].cost_units
        # Shrink window from left until all events span <= window_seconds
        while (
            sorted_events[right].timestamp - sorted_events[left].timestamp
        ).total_seconds() > window_seconds:
            window_cost -= sorted_events[left].cost_units
            left += 1
        if window_cost > best_cost:
            best_cost = window_cost
            best = sorted_events[left : right + 1]
    return best


def _count_baseline(events: list[Event], predicate) -> int:
    return sum(1 for event in events if predicate(event))


def _baseline_error_rate(events: list[Event], tenant_id: str, endpoint: str) -> float:
    matching = [event for event in events if event.tenant_id == tenant_id and event.endpoint == endpoint]
    if not matching:
        return 0.0
    return sum(1 for event in matching if event.status_code >= 500) / len(matching)


def _ratio(observed: float, baseline: float) -> float:
    if baseline <= 0:
        return observed if observed > 0 else 0.0
    return round(observed / baseline, 4)


def _evidence(events: list[Event], *, note: str) -> dict[str, object]:
    sample = events[:5]
    latencies = [event.latency_ms for event in events if event.latency_ms is not None]
    return {
        "sample_request_ids": [event.request_id for event in sample],
        "notes": [note],
        "countries": sorted({event.country for event in sample if event.country}),
        "median_latency_ms": median(latencies) if latencies else None,
    }


def _dominant_value(values: Iterable[str]) -> str | None:
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _anomaly_id(prefix: str, window_start: datetime, subject: str) -> str:
    compact_time = window_start.strftime("%Y%m%d%H%M")
    safe_subject = "".join(char if char.isalnum() or char == "_" else "_" for char in subject)
    return f"anom_{compact_time}_{prefix}_{safe_subject}"
