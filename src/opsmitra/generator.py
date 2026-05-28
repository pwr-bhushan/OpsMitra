"""Synthetic log generation for OpsMitra."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from opsmitra.models import Event


@dataclass(frozen=True)
class Incident:
    kind: str
    tenant_id: str
    start: datetime
    duration_minutes: int
    volume: int
    endpoint: str | None = None
    api_key_id: str | None = None
    ip: str | None = None


class SyntheticLogGenerator:
    def __init__(self, *, seed: int = 1, tenants: tuple[str, ...] | None = None) -> None:
        self._random = random.Random(seed)
        self._tenants = tenants or ("tenant_acme", "tenant_beta", "tenant_cedar")

    def generate(self, *, start: datetime, hours: int, incidents: Iterable[Incident] | None = None) -> list[Event]:
        events: list[Event] = []
        normalized_start = start.astimezone(timezone.utc)

        for minute in range(hours * 60):
            timestamp = normalized_start + timedelta(minutes=minute)
            events.extend(self._normal_minute(timestamp, minute))

        for index, incident in enumerate(incidents or ()):
            events.extend(self._incident_events(incident, index))

        return sorted(events, key=lambda event: (event.timestamp, event.request_id))

    def _normal_minute(self, timestamp: datetime, minute: int) -> list[Event]:
        events: list[Event] = []
        for tenant in self._tenants:
            traffic = self._random.randint(3, 7)
            for offset in range(traffic):
                endpoint = self._random.choice(("/health", "/checkout", "/sms/send", "/auth/login"))
                status_code = self._normal_status(endpoint)
                events.append(
                    Event(
                        timestamp=timestamp + timedelta(seconds=offset),
                        tenant_id=tenant,
                        user_id=f"user_{self._random.randint(1, 80):03d}",
                        api_key_id=f"key_{tenant.split('_')[-1]}_normal",
                        endpoint=endpoint,
                        method="POST" if endpoint != "/health" else "GET",
                        status_code=status_code,
                        latency_ms=self._random.randint(25, 350),
                        ip=f"203.0.113.{self._random.randint(1, 200)}",
                        country=self._random.choice(("IN", "US", "GB", "DE")),
                        cost_units=1.0 if endpoint == "/sms/send" else 0.0,
                        provider="twilio" if endpoint == "/sms/send" else "internal",
                        request_id=f"req_normal_{minute:04d}_{tenant}_{offset:02d}",
                    )
                )
        return events

    def _normal_status(self, endpoint: str) -> int:
        if endpoint == "/auth/login" and self._random.random() < 0.05:
            return 401
        if endpoint == "/checkout" and self._random.random() < 0.01:
            return 500
        return 200

    def _incident_events(self, incident: Incident, incident_index: int) -> list[Event]:
        if incident.volume < 0:
            raise ValueError("incident volume must be zero or positive")
        if incident.duration_minutes <= 0:
            raise ValueError("incident duration must be positive")

        start = incident.start.astimezone(timezone.utc)
        handlers = {
            "sms_abuse": self._sms_abuse_event,
            "auth_failure_burst": self._auth_failure_event,
            "error_rate_spike": self._error_spike_event,
            "cost_runaway": self._cost_runaway_event,
        }
        try:
            handler = handlers[incident.kind]
        except KeyError as exc:
            raise ValueError(f"unsupported incident kind: {incident.kind}") from exc

        return [
            handler(incident, start + timedelta(seconds=index % (incident.duration_minutes * 60)), incident_index, index)
            for index in range(incident.volume)
        ]

    def _sms_abuse_event(self, incident: Incident, timestamp: datetime, incident_index: int, index: int) -> Event:
        return Event(
            timestamp=timestamp,
            tenant_id=incident.tenant_id,
            user_id=f"user_sms_{index % 25:03d}",
            api_key_id=incident.api_key_id or f"key_{incident.tenant_id}_abused",
            endpoint="/sms/send",
            method="POST",
            status_code=200,
            latency_ms=80,
            ip=incident.ip or "198.51.100.77",
            country="IN",
            cost_units=1.0,
            provider="twilio",
            request_id=f"req_inc_{incident_index}_sms_{index:05d}",
        )

    def _auth_failure_event(self, incident: Incident, timestamp: datetime, incident_index: int, index: int) -> Event:
        return Event(
            timestamp=timestamp,
            tenant_id=incident.tenant_id,
            user_id=f"user_auth_{index:04d}",
            api_key_id=None,
            endpoint="/auth/login",
            method="POST",
            status_code=401,
            latency_ms=35,
            ip=incident.ip or "198.51.100.10",
            country="US",
            cost_units=0.0,
            provider="internal",
            request_id=f"req_inc_{incident_index}_auth_{index:05d}",
        )

    def _error_spike_event(self, incident: Incident, timestamp: datetime, incident_index: int, index: int) -> Event:
        return Event(
            timestamp=timestamp,
            tenant_id=incident.tenant_id,
            user_id=f"user_err_{index % 40:03d}",
            api_key_id=f"key_{incident.tenant_id}_normal",
            endpoint=incident.endpoint or "/checkout",
            method="POST",
            status_code=500,
            latency_ms=900,
            ip=incident.ip or "203.0.113.55",
            country="IN",
            cost_units=0.0,
            provider="internal",
            request_id=f"req_inc_{incident_index}_err_{index:05d}",
        )

    def _cost_runaway_event(self, incident: Incident, timestamp: datetime, incident_index: int, index: int) -> Event:
        return Event(
            timestamp=timestamp,
            tenant_id=incident.tenant_id,
            user_id=f"user_job_{index % 10:03d}",
            api_key_id=f"key_{incident.tenant_id}_jobs",
            endpoint=incident.endpoint or "/jobs/process",
            method="POST",
            status_code=200,
            latency_ms=400,
            ip=incident.ip or "203.0.113.88",
            country="IN",
            cost_units=25.0,
            provider="internal",
            request_id=f"req_inc_{incident_index}_cost_{index:05d}",
        )


def write_jsonl(events: Iterable[Event], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event.to_dict(), sort_keys=True))
            handle.write("\n")
