"""Configuration loading for OpsMitra."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal, Mapping, cast

logger = logging.getLogger(__name__)

# Canonical Slack incoming-webhook URL prefix. Used only for shape
# validation (warn-only). Slack Enterprise Grid + relay proxies may use
# alternate domains, so this is informational, not a hard requirement.
_SLACK_HOOK_PREFIX = "https://hooks.slack.com/"

# ---------------------------------------------------------------------------
# DetectorThresholds
# ---------------------------------------------------------------------------

_MAX_THRESHOLDS_FILE_BYTES = 1_000_000  # 1 MB cap — order-of-magnitude headroom for any real config

_THRESHOLD_FIELDS: frozenset[str] = frozenset({
    "sms_abuse_rate_per_minute",
    "auth_burst_count",
    "auth_burst_window_seconds",
    "endpoint_error_rate",
    "endpoint_error_min_samples",
    "cost_runaway_delta_usd",
    "cost_runaway_window_seconds",
})


@dataclass(frozen=True)
class DetectorThresholds:
    """Flat dataclass holding all detector threshold knobs.

    Defaults are taken from the existing detector hardcoded constants so that
    DetectorThresholds() is a drop-in replacement.
    """

    sms_abuse_rate_per_minute: float = 5.0
    auth_burst_count: int = 10
    # TODO(step-10-hardening): wire window seconds into detect_auth_failure_burst /
    # detect_cost_runaway; currently default 60s/300s hardcoded in DetectionConfig.
    auth_burst_window_seconds: int = 60
    endpoint_error_rate: float = 0.10
    endpoint_error_min_samples: int = 20
    cost_runaway_delta_usd: float = 10.0
    cost_runaway_window_seconds: int = 300


def resolve_thresholds(
    overrides: dict[str, Any] | None,
    tenant: str | None,
    endpoint: str | None,
) -> DetectorThresholds:
    """Resolve DetectorThresholds with precedence: endpoint > tenant > default.

    Args:
        overrides: Parsed override dict (from load_thresholds) or None for defaults.
        tenant: Tenant ID to look up in overrides["tenants"] or None.
        endpoint: Endpoint to look up in overrides["endpoints"] or None.

    Returns:
        A merged DetectorThresholds with the highest-precedence value for each field.
    """
    if not overrides:
        return DetectorThresholds()

    # Start from the default level
    merged: dict[str, Any] = {}
    default_overrides = overrides.get("default") or {}
    merged.update(default_overrides)

    # Tenant level overrides default
    if tenant:
        tenant_overrides = (overrides.get("tenants") or {}).get(tenant) or {}
        merged.update(tenant_overrides)

    # Endpoint level overrides tenant
    if endpoint:
        endpoint_overrides = (overrides.get("endpoints") or {}).get(endpoint) or {}
        merged.update(endpoint_overrides)

    # Build from defaults + merged
    defaults = DetectorThresholds()
    field_values = {f.name: getattr(defaults, f.name) for f in fields(defaults)}
    field_values.update(merged)

    return DetectorThresholds(**field_values)


def load_thresholds(path: Path | str | None) -> dict[str, Any] | None:
    """Load and validate thresholds override dict from a JSON file.

    Args:
        path: Path to the JSON thresholds file, or None.

    Returns:
        Parsed dict or None if path is None.

    Raises:
        ValueError: If any field name in the JSON is not a known threshold field.
        FileNotFoundError: If the path doesn't exist.
    """
    if path is None:
        return None

    resolved = Path(path)
    try:
        file_size = resolved.stat().st_size
    except FileNotFoundError:
        raise ValueError("thresholds file not found: %s" % path) from None
    if file_size > _MAX_THRESHOLDS_FILE_BYTES:
        raise ValueError(
            "thresholds file %s exceeds %d bytes" % (path, _MAX_THRESHOLDS_FILE_BYTES)
        )
    text = resolved.read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON in thresholds file: {exc}") from exc

    # Validate all field names across all sections
    for section_key in ("default", "tenants", "endpoints"):
        section = data.get(section_key)
        if not section:
            continue
        if section_key == "default":
            _validate_threshold_fields(section)
        else:
            for _tenant_or_ep, overrides in section.items():
                _validate_threshold_fields(overrides)

    return data


def _validate_threshold_fields(overrides: dict[str, Any]) -> None:
    for name in overrides:
        if name not in _THRESHOLD_FIELDS:
            raise ValueError(f"unknown threshold field: {name!r}")


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalConfig:
    log_path: Path
    output_path: Path


@dataclass(frozen=True)
class AwsConfig:
    region: str
    s3_bucket: str | None
    s3_prefix: str
    athena_database: str
    athena_table: str
    athena_workgroup: str
    athena_output_location: str | None


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    endpoint_url: str
    model_name: str
    timeout_seconds: float


@dataclass(frozen=True)
class AlertConfig:
    slack_webhook_url: str | None
    dry_run: bool
    slack_timeout_seconds: float = 5.0
    slack_max_retries: int = 3
    slack_backoff_base_seconds: float = 0.5
    slack_backoff_max_seconds: float = 8.0
    slack_total_wait_cap_seconds: float = 15.0
    slack_channel_override: str | None = None


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime execution settings (cooldown, event cap)."""

    cooldown_seconds: int = 3600
    cooldown_path: Path = field(default_factory=lambda: Path("./.opsmitra/cooldown.json"))
    max_events_per_window: int = 1_000_000


@dataclass(frozen=True)
class AppConfig:
    local: LocalConfig
    aws: AwsConfig
    model: ModelConfig
    alert: AlertConfig
    event_source_kind: Literal["local", "athena"] = "local"
    event_sink_kind: Literal["local", "s3"] = "local"
    thresholds_path: Path | None = None
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


def _load_runtime_config(values: Mapping[str, str]) -> RuntimeConfig:
    """Build RuntimeConfig from environment values with bounds-checking."""
    cooldown_seconds = _parse_int(_get(values, "OPSMITRA_COOLDOWN_SECONDS", "3600"))
    if cooldown_seconds < 0:
        raise ValueError("OPSMITRA_COOLDOWN_SECONDS must be >= 0")

    cooldown_path_str = _get(values, "OPSMITRA_COOLDOWN_PATH", "./.opsmitra/cooldown.json")
    cooldown_path = Path(cooldown_path_str)

    max_events = _parse_int(_get(values, "OPSMITRA_MAX_EVENTS_PER_WINDOW", "1000000"))
    if max_events <= 0:
        raise ValueError("OPSMITRA_MAX_EVENTS_PER_WINDOW must be > 0")

    return RuntimeConfig(
        cooldown_seconds=cooldown_seconds,
        cooldown_path=cooldown_path,
        max_events_per_window=max_events,
    )


def load_config(env: Mapping[str, str] | None = None) -> AppConfig:
    values = os.environ if env is None else env

    event_source_kind = _get(values, "OPSMITRA_EVENT_SOURCE", "local")
    if event_source_kind not in ("local", "athena"):
        raise ValueError(
            f"OPSMITRA_EVENT_SOURCE must be 'local' or 'athena', got {event_source_kind!r}"
        )

    event_sink_kind = _get(values, "OPSMITRA_EVENT_SINK", "local")
    if event_sink_kind not in ("local", "s3"):
        raise ValueError(
            f"OPSMITRA_EVENT_SINK must be 'local' or 's3', got {event_sink_kind!r}"
        )

    thresholds_path_str = _optional(values, "OPSMITRA_THRESHOLDS_PATH")
    thresholds_path = Path(thresholds_path_str) if thresholds_path_str else None

    return AppConfig(
        local=LocalConfig(
            log_path=Path(_get(values, "OPSMITRA_LOG_PATH", "data/events.jsonl")),
            output_path=Path(_get(values, "OPSMITRA_OUTPUT_PATH", "outputs")),
        ),
        aws=AwsConfig(
            region=_get(values, "OPSMITRA_AWS_REGION", "us-east-1"),
            s3_bucket=_optional(values, "OPSMITRA_S3_BUCKET"),
            s3_prefix=_get(values, "OPSMITRA_S3_PREFIX", "opsmitra-events"),
            athena_database=_get(values, "OPSMITRA_ATHENA_DATABASE", "opsmitra"),
            athena_table=_get(values, "OPSMITRA_ATHENA_TABLE", "events"),
            athena_workgroup=_get(values, "OPSMITRA_ATHENA_WORKGROUP", "primary"),
            athena_output_location=_optional(values, "OPSMITRA_ATHENA_OUTPUT_LOCATION"),
        ),
        model=ModelConfig(
            provider=_get(values, "OPSMITRA_MODEL_PROVIDER", "fallback"),
            endpoint_url=_get(values, "OPSMITRA_MODEL_URL", "http://localhost:11434"),
            model_name=_get(values, "OPSMITRA_MODEL_NAME", "llama3.1:8b"),
            timeout_seconds=_parse_float(_get(values, "OPSMITRA_MODEL_TIMEOUT", "10")),
        ),
        alert=_load_alert_config(values),
        event_source_kind=cast(Literal["local", "athena"], event_source_kind),
        event_sink_kind=cast(Literal["local", "s3"], event_sink_kind),
        thresholds_path=thresholds_path,
        runtime=_load_runtime_config(values),
    )


def _load_alert_config(values: Mapping[str, str]) -> AlertConfig:
    """Build AlertConfig from environment values.

    Warns on unexpected webhook URL prefix and raises ValueError when any
    numeric Slack setting is out of range. Range messages name only the
    offending field — never the URL or its env-var name.
    """
    webhook_url = _optional(values, "OPSMITRA_SLACK_WEBHOOK_URL")
    if webhook_url is not None and not webhook_url.startswith(_SLACK_HOOK_PREFIX):
        logger.warning("OPSMITRA_SLACK_WEBHOOK_URL has unexpected prefix")

    timeout_seconds = _parse_float(
        _get(values, "OPSMITRA_SLACK_TIMEOUT_SECONDS", "5.0")
    )
    max_retries = _parse_int(_get(values, "OPSMITRA_SLACK_MAX_RETRIES", "3"))
    backoff_base = _parse_float(
        _get(values, "OPSMITRA_SLACK_BACKOFF_BASE_SECONDS", "0.5")
    )
    backoff_max = _parse_float(
        _get(values, "OPSMITRA_SLACK_BACKOFF_MAX_SECONDS", "8.0")
    )
    total_wait_cap = _parse_float(
        _get(values, "OPSMITRA_SLACK_TOTAL_WAIT_CAP_SECONDS", "15.0")
    )

    if timeout_seconds <= 0:
        raise ValueError("slack_timeout_seconds must be > 0")
    if max_retries < 0:
        raise ValueError("slack_max_retries must be >= 0")
    if backoff_base < 0:
        raise ValueError("slack_backoff_base_seconds must be >= 0")
    if backoff_max < backoff_base:
        raise ValueError(
            "slack_backoff_max_seconds must be >= slack_backoff_base_seconds"
        )
    if total_wait_cap < 0:
        raise ValueError("slack_total_wait_cap_seconds must be >= 0")

    return AlertConfig(
        slack_webhook_url=webhook_url,
        dry_run=_parse_bool(_get(values, "OPSMITRA_DRY_RUN", "true"), default=True),
        slack_timeout_seconds=timeout_seconds,
        slack_max_retries=max_retries,
        slack_backoff_base_seconds=backoff_base,
        slack_backoff_max_seconds=backoff_max,
        slack_total_wait_cap_seconds=total_wait_cap,
        slack_channel_override=_optional(values, "OPSMITRA_SLACK_CHANNEL_OVERRIDE"),
    )


def _get(values: Mapping[str, str], key: str, default: str) -> str:
    value = values.get(key)
    return default if value is None or value == "" else value


def _optional(values: Mapping[str, str], key: str) -> str | None:
    value = values.get(key)
    return None if value is None or value == "" else value


def _parse_bool(value: str, *, default: bool) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_float(value: str) -> float:
    """Parse a string to a float.

    Args:
        value: String to parse.

    Returns:
        Parsed float.

    Raises:
        ValueError: If the value is not a valid float.
    """
    try:
        return float(value.strip())
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Invalid float value: {value}") from e


def _parse_int(value: str) -> int:
    """Parse a string to an int.

    Args:
        value: String to parse.

    Returns:
        Parsed int.

    Raises:
        ValueError: If the value is not a valid integer.
    """
    try:
        return int(value.strip())
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Invalid int value: {value}") from e
