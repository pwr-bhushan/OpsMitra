"""Domain models for events and anomalies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


VALID_SEVERITIES = {"low", "medium", "high", "critical"}

# Canonical set of event endpoint types — single source of truth shared by
# query_builder.py, detectors, and tests. Extend here when new event types
# are introduced.
EVENT_TYPES: frozenset[str] = frozenset({
    "/sms/send",
    "/auth/login",
    "/checkout",
    "/jobs/process",
})


@dataclass(frozen=True)
class Event:
    timestamp: datetime
    tenant_id: str
    endpoint: str
    method: str
    status_code: int
    cost_units: float
    request_id: str
    user_id: str | None = None
    api_key_id: str | None = None
    latency_ms: int | None = None
    ip: str | None = None
    country: str | None = None
    provider: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Event":
        status_code = int(_required(payload, "status_code"))
        cost_units = float(_required(payload, "cost_units"))

        if status_code < 100 or status_code > 599:
            raise ValueError("status_code must be between 100 and 599")
        if cost_units < 0:
            raise ValueError("cost_units must be zero or positive")

        latency = payload.get("latency_ms")

        return cls(
            timestamp=_parse_utc_datetime(str(_required(payload, "timestamp"))),
            tenant_id=str(_required(payload, "tenant_id")),
            endpoint=str(_required(payload, "endpoint")),
            method=str(_required(payload, "method")),
            status_code=status_code,
            cost_units=cost_units,
            request_id=str(_required(payload, "request_id")),
            user_id=_string_or_none(payload.get("user_id")),
            api_key_id=_string_or_none(payload.get("api_key_id")),
            latency_ms=None if latency is None else int(latency),
            ip=_string_or_none(payload.get("ip")),
            country=_string_or_none(payload.get("country")),
            provider=_string_or_none(payload.get("provider")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": _format_utc_datetime(self.timestamp),
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "api_key_id": self.api_key_id,
            "endpoint": self.endpoint,
            "method": self.method,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "ip": self.ip,
            "country": self.country,
            "cost_units": self.cost_units,
            "provider": self.provider,
            "request_id": self.request_id,
        }


@dataclass(frozen=True)
class Anomaly:
    id: str
    type: str
    severity: str
    window_start: datetime
    window_end: datetime
    subject: dict[str, Any]
    observed: dict[str, Any]
    evidence: dict[str, Any]
    recommended_actions: list[str]
    tenant_id: str | None = None
    baseline: dict[str, Any] | None = None
    ratio: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Anomaly":
        severity = str(_required(payload, "severity"))
        if severity not in VALID_SEVERITIES:
            raise ValueError(f"severity must be one of {sorted(VALID_SEVERITIES)}")

        actions = payload.get("recommended_actions")
        if not isinstance(actions, list) or not all(isinstance(action, str) for action in actions):
            raise ValueError("recommended_actions must be a list of strings")
        if not actions:
            raise ValueError("recommended_actions must not be empty")

        ratio = payload.get("ratio")

        return cls(
            id=str(_required(payload, "id")),
            type=str(_required(payload, "type")),
            severity=severity,
            window_start=_parse_utc_datetime(str(_required(payload, "window_start"))),
            window_end=_parse_utc_datetime(str(_required(payload, "window_end"))),
            tenant_id=_string_or_none(payload.get("tenant_id")),
            subject=_dict_required(payload, "subject"),
            observed=_dict_required(payload, "observed"),
            baseline=payload.get("baseline"),
            ratio=None if ratio is None else float(ratio),
            evidence=_dict_required(payload, "evidence"),
            recommended_actions=actions,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "severity": self.severity,
            "window_start": _format_utc_datetime(self.window_start),
            "window_end": _format_utc_datetime(self.window_end),
            "tenant_id": self.tenant_id,
            "subject": self.subject,
            "observed": self.observed,
            "baseline": self.baseline,
            "ratio": self.ratio,
            "evidence": self.evidence,
            "recommended_actions": self.recommended_actions,
        }


def _parse_utc_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _required(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _dict_required(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = _required(payload, key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _string_or_none(value: Any) -> str | None:
    return None if value is None else str(value)
