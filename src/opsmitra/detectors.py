"""Deterministic anomaly detectors."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Iterable

from opsmitra.models import Anomaly, Event


@dataclass(frozen=True)
class DetectionConfig:
    sms_min_count: int = 500
    sms_ratio_threshold: float = 5.0
    auth_min_failures: int = 100
    auth_min_users: int = 20
    error_min_requests: int = 50
    error_rate_threshold: float = 0.35
    cost_min_units: float = 1_000.0
    cost_ratio_threshold: float = 10.0


def detect_anomalies(
    events: Iterable[Event],
    window_start: datetime,
    window_end: datetime,
    config: DetectionConfig | None = None,
) -> list[Anomaly]:
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
        distinct_users = len({event.user_id for event in grouped if event.user_id})
        if len(grouped) >= config.auth_min_failures and distinct_users >= config.auth_min_users:
            tenant_id = _dominant_value(event.tenant_id for event in grouped)
            anomalies.append(
                Anomaly(
                    id=_anomaly_id("auth_failure", window_start, ip.replace(".", "_")),
                    type="auth_failure_burst",
                    severity="high",
                    window_start=window_start,
                    window_end=window_end,
                    tenant_id=tenant_id,
                    subject={"ip": ip, "endpoint": "/auth/login"},
                    observed={"failure_count": len(grouped), "distinct_users": distinct_users},
                    baseline=None,
                    ratio=None,
                    evidence=_evidence(grouped, note="one IP failed authentication across many users"),
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
        observed_cost = sum(event.cost_units for event in grouped)
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
                    subject={"endpoint": endpoint, "provider": _dominant_value(event.provider or "unknown" for event in grouped)},
                    observed={"event_count": len(grouped), "cost_units": observed_cost},
                    baseline={"cost_units": baseline_cost},
                    ratio=ratio,
                    evidence=_evidence(grouped, note="tenant workload consumed unusual cost units"),
                    recommended_actions=[
                        "Pause or throttle the workload",
                        "Inspect job retry loops",
                        "Set temporary spend guardrails",
                    ],
                )
            )
    return anomalies


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
