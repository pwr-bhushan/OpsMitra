import json
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


# ---------------------------------------------------------------------------
# AlertConfig: Slack delivery fields — defaults, env overrides, malformed int
# ---------------------------------------------------------------------------


def test_alert_config_slack_defaults():
    """New Slack numeric/optional fields must use plan-documented defaults."""
    config = load_config({})

    assert config.alert.slack_timeout_seconds == 5.0
    assert config.alert.slack_max_retries == 3
    assert config.alert.slack_backoff_base_seconds == 0.5
    assert config.alert.slack_backoff_max_seconds == 8.0
    assert config.alert.slack_total_wait_cap_seconds == 15.0
    assert config.alert.slack_channel_override is None


def test_alert_config_slack_env_overrides():
    """All Slack env vars must override their AlertConfig fields with correct types."""
    env = {
        "OPSMITRA_SLACK_TIMEOUT_SECONDS": "2.5",
        "OPSMITRA_SLACK_MAX_RETRIES": "5",
        "OPSMITRA_SLACK_BACKOFF_BASE_SECONDS": "1.0",
        "OPSMITRA_SLACK_BACKOFF_MAX_SECONDS": "12.0",
        "OPSMITRA_SLACK_TOTAL_WAIT_CAP_SECONDS": "30.0",
        "OPSMITRA_SLACK_CHANNEL_OVERRIDE": "#ops-alerts",
    }
    config = load_config(env)

    assert config.alert.slack_timeout_seconds == 2.5
    assert config.alert.slack_max_retries == 5
    assert config.alert.slack_backoff_base_seconds == 1.0
    assert config.alert.slack_backoff_max_seconds == 12.0
    assert config.alert.slack_total_wait_cap_seconds == 30.0
    assert config.alert.slack_channel_override == "#ops-alerts"


def test_alert_config_slack_max_retries_malformed_raises_value_error():
    """OPSMITRA_SLACK_MAX_RETRIES='abc' must raise ValueError (lessons.md: fail loud)."""
    with pytest.raises(ValueError):
        load_config({"OPSMITRA_SLACK_MAX_RETRIES": "abc"})


# ---------------------------------------------------------------------------
# AlertConfig bounds checking (H2)
# ---------------------------------------------------------------------------


def test_alert_config_timeout_zero_raises_value_error():
    """OPSMITRA_SLACK_TIMEOUT_SECONDS='0' must raise ValueError naming the field."""
    with pytest.raises(ValueError, match="slack_timeout_seconds"):
        load_config({"OPSMITRA_SLACK_TIMEOUT_SECONDS": "0"})


def test_alert_config_timeout_negative_raises_value_error():
    """OPSMITRA_SLACK_TIMEOUT_SECONDS='-1.5' must raise ValueError naming the field."""
    with pytest.raises(ValueError, match="slack_timeout_seconds"):
        load_config({"OPSMITRA_SLACK_TIMEOUT_SECONDS": "-1.5"})


def test_alert_config_max_retries_negative_raises_value_error():
    """OPSMITRA_SLACK_MAX_RETRIES='-1' must raise ValueError naming the field."""
    with pytest.raises(ValueError, match="slack_max_retries"):
        load_config({"OPSMITRA_SLACK_MAX_RETRIES": "-1"})


def test_alert_config_backoff_base_negative_raises_value_error():
    """OPSMITRA_SLACK_BACKOFF_BASE_SECONDS='-0.1' must raise ValueError naming the field."""
    with pytest.raises(ValueError, match="slack_backoff_base_seconds"):
        load_config({"OPSMITRA_SLACK_BACKOFF_BASE_SECONDS": "-0.1"})


def test_alert_config_backoff_max_below_base_raises_value_error():
    """backoff_max < backoff_base must raise ValueError mentioning both fields."""
    with pytest.raises(ValueError, match="slack_backoff_max_seconds"):
        load_config(
            {
                "OPSMITRA_SLACK_BACKOFF_BASE_SECONDS": "2.0",
                "OPSMITRA_SLACK_BACKOFF_MAX_SECONDS": "1.0",
            }
        )


def test_alert_config_total_wait_cap_negative_raises_value_error():
    """OPSMITRA_SLACK_TOTAL_WAIT_CAP_SECONDS='-5' must raise ValueError naming the field."""
    with pytest.raises(ValueError, match="slack_total_wait_cap_seconds"):
        load_config({"OPSMITRA_SLACK_TOTAL_WAIT_CAP_SECONDS": "-5"})


def test_alert_config_error_messages_never_contain_url():
    """All six out-of-range errors must not include 'hooks.slack.com' or a sentinel URL."""
    sentinel_url = "https://hooks.slack.com/services/SECRET-SENTINEL"
    bad_configs = [
        {"OPSMITRA_SLACK_TIMEOUT_SECONDS": "0"},
        {"OPSMITRA_SLACK_TIMEOUT_SECONDS": "-1"},
        {"OPSMITRA_SLACK_MAX_RETRIES": "-1"},
        {"OPSMITRA_SLACK_BACKOFF_BASE_SECONDS": "-0.1"},
        {
            "OPSMITRA_SLACK_BACKOFF_BASE_SECONDS": "2.0",
            "OPSMITRA_SLACK_BACKOFF_MAX_SECONDS": "1.0",
        },
        {"OPSMITRA_SLACK_TOTAL_WAIT_CAP_SECONDS": "-5"},
    ]
    for env_override in bad_configs:
        env = {"OPSMITRA_SLACK_WEBHOOK_URL": sentinel_url, **env_override}
        with pytest.raises(ValueError) as exc_info:
            load_config(env)
        msg = str(exc_info.value)
        assert "hooks.slack.com" not in msg, f"URL leaked in: {msg!r}"
        assert "SECRET-SENTINEL" not in msg, f"Sentinel URL leaked in: {msg!r}"


# ---------------------------------------------------------------------------
# Step 7 new env vars — OPSMITRA_EVENT_SOURCE / OPSMITRA_EVENT_SINK
# ---------------------------------------------------------------------------


def test_event_source_defaults_to_local():
    """OPSMITRA_EVENT_SOURCE must default to 'local' when unset."""
    config = load_config({})
    assert config.event_source_kind == "local"


def test_event_source_accepts_athena():
    """OPSMITRA_EVENT_SOURCE='athena' must be stored as 'athena'."""
    config = load_config({"OPSMITRA_EVENT_SOURCE": "athena"})
    assert config.event_source_kind == "athena"


def test_event_source_rejects_unknown_value():
    """OPSMITRA_EVENT_SOURCE='kafka' must raise ValueError (only local/athena allowed)."""
    with pytest.raises(ValueError):
        load_config({"OPSMITRA_EVENT_SOURCE": "kafka"})


def test_event_sink_defaults_to_local():
    """OPSMITRA_EVENT_SINK must default to 'local' when unset."""
    config = load_config({})
    assert config.event_sink_kind == "local"


def test_event_sink_accepts_s3():
    """OPSMITRA_EVENT_SINK='s3' must be stored as 's3'."""
    config = load_config({"OPSMITRA_EVENT_SINK": "s3"})
    assert config.event_sink_kind == "s3"


def test_event_sink_rejects_unknown_value():
    """OPSMITRA_EVENT_SINK='gcs' must raise ValueError (only local/s3 allowed)."""
    with pytest.raises(ValueError):
        load_config({"OPSMITRA_EVENT_SINK": "gcs"})


def test_s3_bucket_defaults_to_none():
    """OPSMITRA_S3_BUCKET must default to None when unset."""
    config = load_config({})
    assert config.aws.s3_bucket is None


def test_s3_bucket_reads_env_var():
    """OPSMITRA_S3_BUCKET must be read into aws.s3_bucket."""
    config = load_config({"OPSMITRA_S3_BUCKET": "my-events-bucket"})
    assert config.aws.s3_bucket == "my-events-bucket"


def test_athena_output_location_defaults_to_none():
    """OPSMITRA_ATHENA_OUTPUT_LOCATION must default to None when unset."""
    config = load_config({})
    assert config.aws.athena_output_location is None


def test_athena_output_location_reads_env_var():
    """OPSMITRA_ATHENA_OUTPUT_LOCATION must be stored under aws config."""
    config = load_config({"OPSMITRA_ATHENA_OUTPUT_LOCATION": "s3://query-results/"})
    assert config.aws.athena_output_location == "s3://query-results/"


def test_thresholds_path_defaults_to_none():
    """OPSMITRA_THRESHOLDS_PATH must default to None when unset (absent → defaults only)."""
    config = load_config({})
    assert config.thresholds_path is None


def test_thresholds_path_reads_env_var(tmp_path: Path):
    """OPSMITRA_THRESHOLDS_PATH set to a valid JSON file must be stored as a Path."""
    thresholds_file = tmp_path / "thresholds.json"
    thresholds_file.write_text(json.dumps({"default": {}}))

    config = load_config({"OPSMITRA_THRESHOLDS_PATH": str(thresholds_file)})
    assert config.thresholds_path == thresholds_file


# ---------------------------------------------------------------------------
# Step 7 fix-pass: thresholds file bounds (M1, M2)
# ---------------------------------------------------------------------------


def test_thresholds_file_too_large_rejected(tmp_path: Path):
    """A thresholds file exceeding 1 MB must raise ValueError before reading content."""
    from opsmitra.config import load_thresholds

    big_file = tmp_path / "big_thresholds.json"
    # Write slightly over 1 MB of data
    big_file.write_bytes(b"x" * 1_100_000)

    with pytest.raises(ValueError, match="exceeds"):
        load_thresholds(big_file)


def test_thresholds_file_missing_raises_friendly_value_error(tmp_path: Path):
    """A non-existent thresholds path must raise ValueError mentioning 'not found', not FileNotFoundError."""
    from opsmitra.config import load_thresholds

    missing = tmp_path / "does_not_exist.json"

    with pytest.raises(ValueError, match="not found"):
        load_thresholds(missing)


# ---------------------------------------------------------------------------
# Step 8 RED-phase: RuntimeConfig env vars
# ---------------------------------------------------------------------------


def test_cooldown_seconds_default_and_env_override():
    """OPSMITRA_COOLDOWN_SECONDS defaults to 3600; env override is parsed correctly;
    negative value raises ValueError."""
    config_default = load_config({})
    assert config_default.runtime.cooldown_seconds == 3600

    config_override = load_config({"OPSMITRA_COOLDOWN_SECONDS": "7200"})
    assert config_override.runtime.cooldown_seconds == 7200

    with pytest.raises(ValueError):
        load_config({"OPSMITRA_COOLDOWN_SECONDS": "-1"})


def test_cooldown_path_default_and_env_override():
    """OPSMITRA_COOLDOWN_PATH defaults to './.opsmitra/cooldown.json'; env overrides it."""
    config_default = load_config({})
    assert config_default.runtime.cooldown_path == Path("./.opsmitra/cooldown.json")

    config_override = load_config({"OPSMITRA_COOLDOWN_PATH": "/tmp/my_cooldown.json"})
    assert config_override.runtime.cooldown_path == Path("/tmp/my_cooldown.json")


def test_max_events_per_window_bounds():
    """OPSMITRA_MAX_EVENTS_PER_WINDOW defaults to 1_000_000; negative and zero raise ValueError."""
    config_default = load_config({})
    assert config_default.runtime.max_events_per_window == 1_000_000

    with pytest.raises(ValueError):
        load_config({"OPSMITRA_MAX_EVENTS_PER_WINDOW": "-1"})

    with pytest.raises(ValueError):
        load_config({"OPSMITRA_MAX_EVENTS_PER_WINDOW": "0"})
