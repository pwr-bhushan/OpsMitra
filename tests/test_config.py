from pathlib import Path

import pytest

from opsmitra.config import AppConfig, load_config


def test_load_config_uses_safe_local_defaults(monkeypatch):
    monkeypatch.delenv("OPSMITRA_SLACK_WEBHOOK_URL", raising=False)

    config = load_config({})

    assert isinstance(config, AppConfig)
    assert config.local.log_path == Path("data/events.jsonl")
    assert config.local.output_path == Path("outputs")
    assert config.alert.dry_run is True
    assert config.alert.slack_webhook_url is None
    assert config.aws.s3_bucket is None


def test_load_config_reads_environment_without_hardcoded_secrets(monkeypatch):
    env = {
        "OPSMITRA_LOG_PATH": "fixtures/events.jsonl",
        "OPSMITRA_OUTPUT_PATH": "tmp/out",
        "OPSMITRA_AWS_REGION": "ap-south-1",
        "OPSMITRA_S3_BUCKET": "opsmitra-dev-logs",
        "OPSMITRA_ATHENA_DATABASE": "opsmitra",
        "OPSMITRA_ATHENA_TABLE": "events",
        "OPSMITRA_ATHENA_OUTPUT_LOCATION": "s3://query-results/",
        "OPSMITRA_MODEL_PROVIDER": "ollama",
        "OPSMITRA_MODEL_URL": "http://localhost:11434",
        "OPSMITRA_MODEL_NAME": "llama3.1",
        "OPSMITRA_SLACK_WEBHOOK_URL": "https://hooks.slack.test/example",
        "OPSMITRA_DRY_RUN": "false",
    }

    config = load_config(env)

    assert config.local.log_path == Path("fixtures/events.jsonl")
    assert config.local.output_path == Path("tmp/out")
    assert config.aws.region == "ap-south-1"
    assert config.aws.s3_bucket == "opsmitra-dev-logs"
    assert config.aws.athena_database == "opsmitra"
    assert config.aws.athena_table == "events"
    assert config.aws.athena_output_location == "s3://query-results/"
    assert config.model.provider == "ollama"
    assert config.model.endpoint_url == "http://localhost:11434"
    assert config.model.model_name == "llama3.1"
    assert config.alert.slack_webhook_url == "https://hooks.slack.test/example"
    assert config.alert.dry_run is False


def test_dry_run_parser_defaults_to_safe_true_for_unknown_values():
    config = load_config({"OPSMITRA_DRY_RUN": "unexpected"})

    assert config.alert.dry_run is True


# ---------------------------------------------------------------------------
# ModelConfig: defaults, env overrides, and malformed timeout handling
# ---------------------------------------------------------------------------


def test_config_loads_model_defaults_when_env_unset():
    """When model env vars are absent, config uses documented defaults."""
    config = load_config({})

    assert config.model.model_name == "llama3.1:8b"
    assert config.model.endpoint_url == "http://localhost:11434"
    assert config.model.timeout_seconds == 10.0


def test_config_reads_model_env_overrides():
    """OPSMITRA_MODEL_NAME, OPSMITRA_MODEL_URL, and OPSMITRA_MODEL_TIMEOUT override defaults."""
    env = {
        "OPSMITRA_MODEL_NAME": "mistral:7b",
        "OPSMITRA_MODEL_URL": "http://10.0.0.5:11434",
        "OPSMITRA_MODEL_TIMEOUT": "3.5",
    }
    config = load_config(env)

    assert config.model.model_name == "mistral:7b"
    assert config.model.endpoint_url == "http://10.0.0.5:11434"
    assert config.model.timeout_seconds == 3.5


def test_config_rejects_malformed_model_timeout():
    """When OPSMITRA_MODEL_TIMEOUT is not a valid number, config raises ValueError."""
    with pytest.raises((ValueError, TypeError)):
        load_config({"OPSMITRA_MODEL_TIMEOUT": "not-a-number"})
