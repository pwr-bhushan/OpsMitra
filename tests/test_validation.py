"""Tests for the Step 11 env validation preflight.

Covers:
- Mode auto-detection from environment (model, slack, aws-source, aws-sink, runtime, eval).
- Required vs optional env vars per mode.
- Cross-field invariants (dry_run=false -> webhook required, etc.).
- Secret redaction (webhook URL value never appears in rendered reports).
- Output formatters (table + json round-trip).
- CLI subcommand exit codes and --strict gating.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from opsmitra.config import load_config
from opsmitra.validation import (
    ALL_MODES,
    CheckResult,
    ModeReport,
    ValidationReport,
    format_report_json,
    format_report_table,
    validate,
)


_BASE_ENV = {
    "OPSMITRA_DRY_RUN": "true",
    "OPSMITRA_EVENT_SOURCE": "local",
    "OPSMITRA_EVENT_SINK": "local",
    "OPSMITRA_MODEL_PROVIDER": "fallback",
}


def _env(**overrides: str) -> dict[str, str]:
    env = dict(_BASE_ENV)
    env.update(overrides)
    return env


# ---------- mode auto-detection ----------


def test_validate_default_all_modes_returns_six_reports() -> None:
    env = _env()
    report = validate(env=env, config=load_config(env))
    assert tuple(m.mode for m in report.modes) == ALL_MODES
    assert len(report.modes) == 6


def test_validate_slack_mode_inactive_when_dry_run_true() -> None:
    env = _env(OPSMITRA_DRY_RUN="true")
    report = validate(env=env, config=load_config(env))
    slack = next(m for m in report.modes if m.mode == "slack")
    assert slack.active is False
    assert slack.status == "ok"


def test_validate_slack_mode_active_when_dry_run_false() -> None:
    env = _env(
        OPSMITRA_DRY_RUN="false",
        OPSMITRA_SLACK_WEBHOOK_URL="https://hooks.slack.com/services/X/Y/Z",
    )
    report = validate(env=env, config=load_config(env))
    slack = next(m for m in report.modes if m.mode == "slack")
    assert slack.active is True
    assert slack.status == "ok"


def test_validate_slack_mode_active_missing_webhook_errors() -> None:
    env = _env(OPSMITRA_DRY_RUN="false")
    report = validate(env=env, config=load_config(env))
    slack = next(m for m in report.modes if m.mode == "slack")
    assert slack.active is True
    assert slack.status == "error"
    error_checks = [c for c in slack.checks if c.status == "error"]
    assert any(c.var_name == "OPSMITRA_SLACK_WEBHOOK_URL" for c in error_checks)


def test_validate_aws_source_inactive_when_local() -> None:
    env = _env(OPSMITRA_EVENT_SOURCE="local")
    report = validate(env=env, config=load_config(env))
    aws_src = next(m for m in report.modes if m.mode == "aws-source")
    assert aws_src.active is False


def test_validate_aws_source_active_missing_output_location_errors() -> None:
    env = _env(OPSMITRA_EVENT_SOURCE="athena")
    report = validate(env=env, config=load_config(env))
    aws_src = next(m for m in report.modes if m.mode == "aws-source")
    assert aws_src.active is True
    assert aws_src.status == "error"
    assert any(
        c.var_name == "OPSMITRA_ATHENA_OUTPUT_LOCATION" and c.status == "error"
        for c in aws_src.checks
    )


def test_validate_aws_source_active_with_output_location_ok() -> None:
    env = _env(
        OPSMITRA_EVENT_SOURCE="athena",
        OPSMITRA_ATHENA_OUTPUT_LOCATION="s3://my-output/results/",
    )
    report = validate(env=env, config=load_config(env))
    aws_src = next(m for m in report.modes if m.mode == "aws-source")
    assert aws_src.active is True
    assert aws_src.status == "ok"


def test_validate_aws_sink_active_missing_bucket_errors() -> None:
    env = _env(OPSMITRA_EVENT_SINK="s3")
    report = validate(env=env, config=load_config(env))
    aws_sink = next(m for m in report.modes if m.mode == "aws-sink")
    assert aws_sink.active is True
    assert aws_sink.status == "error"
    assert any(
        c.var_name == "OPSMITRA_S3_BUCKET" and c.status == "error"
        for c in aws_sink.checks
    )


def test_validate_aws_sink_active_with_bucket_ok() -> None:
    env = _env(OPSMITRA_EVENT_SINK="s3", OPSMITRA_S3_BUCKET="my-events-bucket")
    report = validate(env=env, config=load_config(env))
    aws_sink = next(m for m in report.modes if m.mode == "aws-sink")
    assert aws_sink.active is True
    assert aws_sink.status == "ok"


def test_validate_model_mode_fallback_inactive() -> None:
    env = _env(OPSMITRA_MODEL_PROVIDER="fallback")
    report = validate(env=env, config=load_config(env))
    model = next(m for m in report.modes if m.mode == "model")
    assert model.active is False
    assert model.status == "ok"


def test_validate_model_mode_ollama_active_with_defaults_ok() -> None:
    env = _env(OPSMITRA_MODEL_PROVIDER="ollama")
    report = validate(env=env, config=load_config(env))
    model = next(m for m in report.modes if m.mode == "model")
    assert model.active is True
    # url/name/timeout all have defaults; warnings allowed, but no errors
    assert model.status in ("ok", "warning")


def test_validate_runtime_always_active() -> None:
    env = _env()
    report = validate(env=env, config=load_config(env))
    runtime = next(m for m in report.modes if m.mode == "runtime")
    assert runtime.active is True


def test_validate_eval_always_active() -> None:
    env = _env()
    report = validate(env=env, config=load_config(env))
    eval_mode = next(m for m in report.modes if m.mode == "eval")
    assert eval_mode.active is True


# ---------- mode filtering ----------


def test_validate_filters_modes_when_requested() -> None:
    env = _env()
    report = validate(env=env, config=load_config(env), modes=("slack",))
    assert tuple(m.mode for m in report.modes) == ("slack",)


def test_validate_filters_two_modes() -> None:
    env = _env()
    report = validate(env=env, config=load_config(env), modes=("model", "runtime"))
    assert {m.mode for m in report.modes} == {"model", "runtime"}


# ---------- overall status aggregation ----------


def test_overall_status_ok_when_all_modes_ok() -> None:
    env = _env()
    report = validate(env=env, config=load_config(env))
    assert report.overall_status == "ok"


def test_overall_status_error_when_any_active_mode_errors() -> None:
    env = _env(OPSMITRA_DRY_RUN="false")  # slack active, webhook missing
    report = validate(env=env, config=load_config(env))
    assert report.overall_status == "error"


# ---------- secret redaction ----------


def test_webhook_url_value_never_appears_in_json_report() -> None:
    sentinel = "https://hooks.slack.com/services/SENTINEL-VAL-12345"
    env = _env(OPSMITRA_DRY_RUN="false", OPSMITRA_SLACK_WEBHOOK_URL=sentinel)
    report = validate(env=env, config=load_config(env))
    rendered = format_report_json(report)
    assert "SENTINEL-VAL-12345" not in rendered
    # but the var name should be there
    assert "OPSMITRA_SLACK_WEBHOOK_URL" in rendered


def test_webhook_url_value_never_appears_in_table_report() -> None:
    sentinel = "https://hooks.slack.com/services/SENTINEL-VAL-67890"
    env = _env(OPSMITRA_DRY_RUN="false", OPSMITRA_SLACK_WEBHOOK_URL=sentinel)
    report = validate(env=env, config=load_config(env))
    rendered = format_report_table(report)
    assert "SENTINEL-VAL-67890" not in rendered


def test_thresholds_path_value_appears_in_report() -> None:
    # Non-secret paths should be visible to help debugging
    env = _env(OPSMITRA_THRESHOLDS_PATH="/tmp/my-thresholds.json")
    report = validate(env=env, config=load_config(env))
    rendered = format_report_json(report)
    assert "/tmp/my-thresholds.json" in rendered


# ---------- output formatting ----------


def test_format_report_json_round_trips() -> None:
    env = _env()
    report = validate(env=env, config=load_config(env))
    parsed = json.loads(format_report_json(report))
    assert "modes" in parsed
    assert "overall_status" in parsed
    assert len(parsed["modes"]) == 6


def test_format_report_table_has_columns() -> None:
    env = _env()
    report = validate(env=env, config=load_config(env))
    rendered = format_report_table(report).lower()
    for col in ("mode", "active", "status"):
        assert col in rendered


# ---------- dataclass invariants ----------


def test_check_result_is_frozen() -> None:
    check = CheckResult(var_name="OPSMITRA_X", status="ok", message="m")
    with pytest.raises(Exception):
        check.status = "error"  # type: ignore[misc]


def test_validation_report_is_frozen() -> None:
    env = _env()
    report = validate(env=env, config=load_config(env))
    with pytest.raises(Exception):
        report.overall_status = "error"  # type: ignore[misc]


# ---------- CLI subcommand ----------


def _run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "opsmitra", *args],
        capture_output=True,
        text=True,
        env=full_env,
        cwd=str(Path(__file__).parent.parent),
    )


def test_cli_validate_help_exits_zero() -> None:
    result = _run_cli(["validate", "--help"], env={})
    assert result.returncode == 0
    assert "mode" in result.stdout.lower()


def test_cli_validate_clean_env_exits_zero() -> None:
    result = _run_cli(["validate"], env=_BASE_ENV)
    assert result.returncode == 0


def test_cli_validate_strict_exits_one_when_slack_active_missing_webhook() -> None:
    env = dict(_BASE_ENV)
    env["OPSMITRA_DRY_RUN"] = "false"
    # Override any inherited webhook URL from real env
    env["OPSMITRA_SLACK_WEBHOOK_URL"] = ""
    result = _run_cli(["validate", "--strict"], env=env)
    assert result.returncode == 1


def test_cli_validate_non_strict_exits_zero_even_with_errors() -> None:
    env = dict(_BASE_ENV)
    env["OPSMITRA_DRY_RUN"] = "false"
    env["OPSMITRA_SLACK_WEBHOOK_URL"] = ""
    result = _run_cli(["validate"], env=env)
    # Without --strict, errors are reported but exit stays 0
    assert result.returncode == 0


def test_cli_validate_report_json_emits_valid_json() -> None:
    result = _run_cli(["validate", "--report", "json"], env=_BASE_ENV)
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert "modes" in parsed


def test_cli_validate_filter_single_mode() -> None:
    result = _run_cli(
        ["validate", "--mode", "slack", "--report", "json"], env=_BASE_ENV
    )
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert len(parsed["modes"]) == 1
    assert parsed["modes"][0]["mode"] == "slack"


def test_cli_validate_filter_multiple_modes() -> None:
    result = _run_cli(
        ["validate", "--mode", "model,runtime", "--report", "json"], env=_BASE_ENV
    )
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert {m["mode"] for m in parsed["modes"]} == {"model", "runtime"}


def test_cli_validate_webhook_url_never_in_stdout() -> None:
    sentinel = "https://hooks.slack.com/services/SENTINEL-CLI-99999"
    env = dict(_BASE_ENV)
    env["OPSMITRA_DRY_RUN"] = "false"
    env["OPSMITRA_SLACK_WEBHOOK_URL"] = sentinel
    result = _run_cli(["validate", "--report", "json"], env=env)
    assert "SENTINEL-CLI-99999" not in result.stdout
    assert "SENTINEL-CLI-99999" not in result.stderr


def test_cli_validate_invalid_mode_exits_64() -> None:
    result = _run_cli(["validate", "--mode", "nosuchmode"], env=_BASE_ENV)
    # argparse misuse -> exit 64 (EX_USAGE) per Step 10 M2
    assert result.returncode == 64


# ---------- parse_modes_arg ----------


def test_parse_modes_arg_all_returns_all_modes() -> None:
    from opsmitra.validation import parse_modes_arg

    assert parse_modes_arg("all") == ALL_MODES


def test_parse_modes_arg_single_mode() -> None:
    from opsmitra.validation import parse_modes_arg

    assert parse_modes_arg("slack") == ("slack",)


def test_parse_modes_arg_comma_separated_with_spaces() -> None:
    from opsmitra.validation import parse_modes_arg

    assert parse_modes_arg("model, slack") == ("model", "slack")


def test_parse_modes_arg_rejects_unknown_mode_with_clear_error() -> None:
    from opsmitra.validation import parse_modes_arg

    with pytest.raises(ValueError, match="unknown mode 'nosuchmode'"):
        parse_modes_arg("nosuchmode")


# ---------- validate() uses os.environ when env=None ----------


def test_validate_with_none_env_uses_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force a known mode-detection signal via os.environ
    monkeypatch.setenv("OPSMITRA_DRY_RUN", "true")
    monkeypatch.setenv("OPSMITRA_EVENT_SOURCE", "local")
    monkeypatch.setenv("OPSMITRA_EVENT_SINK", "local")
    monkeypatch.setenv("OPSMITRA_MODEL_PROVIDER", "fallback")
    report = validate()
    assert report.overall_status == "ok"


# ---------- formatter handles inactive modes ----------


def test_table_summary_shows_inactive_for_inactive_modes() -> None:
    env = _env()  # All defaults -> aws-source/aws-sink/slack/model inactive
    report = validate(env=env, config=load_config(env))
    rendered = format_report_table(report)
    assert "(inactive)" in rendered


def test_table_summary_first_problem_when_mode_has_warning() -> None:
    # Activate model mode w/o env overrides -> warnings on URL/NAME/TIMEOUT
    env = _env(OPSMITRA_MODEL_PROVIDER="ollama")
    report = validate(env=env, config=load_config(env))
    rendered = format_report_table(report)
    assert "OPSMITRA_MODEL_URL" in rendered
    # The summary row for the model mode should report the first warning
    assert "warning" in rendered.lower()


def test_slack_channel_override_appears_in_report_when_set() -> None:
    env = _env(
        OPSMITRA_DRY_RUN="false",
        OPSMITRA_SLACK_WEBHOOK_URL="https://hooks.slack.com/services/X/Y/Z",
        OPSMITRA_SLACK_CHANNEL_OVERRIDE="#oncall",
    )
    report = validate(env=env, config=load_config(env))
    rendered = format_report_json(report)
    assert "#oncall" in rendered
    assert "OPSMITRA_SLACK_CHANNEL_OVERRIDE" in rendered
