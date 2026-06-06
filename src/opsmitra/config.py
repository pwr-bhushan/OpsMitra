"""Configuration loading for OpsMitra."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class LocalConfig:
    log_path: Path
    output_path: Path


@dataclass(frozen=True)
class AwsConfig:
    region: str | None
    s3_bucket: str | None
    athena_database: str | None
    athena_table: str | None
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


@dataclass(frozen=True)
class AppConfig:
    local: LocalConfig
    aws: AwsConfig
    model: ModelConfig
    alert: AlertConfig


def load_config(env: Mapping[str, str] | None = None) -> AppConfig:
    values = os.environ if env is None else env

    return AppConfig(
        local=LocalConfig(
            log_path=Path(_get(values, "OPSMITRA_LOG_PATH", "data/events.jsonl")),
            output_path=Path(_get(values, "OPSMITRA_OUTPUT_PATH", "outputs")),
        ),
        aws=AwsConfig(
            region=_optional(values, "OPSMITRA_AWS_REGION"),
            s3_bucket=_optional(values, "OPSMITRA_S3_BUCKET"),
            athena_database=_optional(values, "OPSMITRA_ATHENA_DATABASE"),
            athena_table=_optional(values, "OPSMITRA_ATHENA_TABLE"),
            athena_output_location=_optional(values, "OPSMITRA_ATHENA_OUTPUT_LOCATION"),
        ),
        model=ModelConfig(
            provider=_get(values, "OPSMITRA_MODEL_PROVIDER", "fallback"),
            endpoint_url=_get(values, "OPSMITRA_MODEL_URL", "http://localhost:11434"),
            model_name=_get(values, "OPSMITRA_MODEL_NAME", "llama3.1:8b"),
            timeout_seconds=_parse_float(_get(values, "OPSMITRA_MODEL_TIMEOUT", "10")),
        ),
        alert=AlertConfig(
            slack_webhook_url=_optional(values, "OPSMITRA_SLACK_WEBHOOK_URL"),
            dry_run=_parse_bool(_get(values, "OPSMITRA_DRY_RUN", "true"), default=True),
        ),
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
