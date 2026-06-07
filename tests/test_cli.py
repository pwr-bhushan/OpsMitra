"""Tests for opsmitra.cli — existing tests plus Step 8 RED-phase extensions.

Step 8 tests import opsmitra.runtime and opsmitra.cooldown indirectly via
opsmitra.cli.main; those imports are expected to fail (ImportError) at
collection time until those modules are implemented.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from opsmitra.cli import build_parser, main


# ---------------------------------------------------------------------------
# Pre-existing test (Step 1)
# ---------------------------------------------------------------------------


def test_cli_exposes_version_and_run_command():
    parser = build_parser()

    parsed = parser.parse_args(["run", "--source", "local", "--window-minutes", "60", "--dry-run"])

    assert parsed.command == "run"
    assert parsed.source == "local"
    assert parsed.window_minutes == 60
    assert parsed.dry_run is True


# ---------------------------------------------------------------------------
# Step 8 RED-phase CLI tests
# ---------------------------------------------------------------------------


def test_cli_run_subcommand_help():
    """'opsmitra run --help' must exit 0 and print the documented flags."""
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--help"])

    assert exc_info.value.code == 0


def test_cli_run_dry_run_no_anomalies_exits_zero(tmp_path: Path):
    """CLI with --dry-run against a source with no anomaly events must exit 0."""
    from opsmitra.runtime import RuntimeResult

    fake_result = RuntimeResult(
        anomalies_detected=0,
        anomalies_alerted=0,
        anomalies_suppressed=0,
        source_kind="local",
        sink_kind="stdout",
        dry_run=True,
        errors=[],
    )

    with patch("opsmitra.cli._compose_and_execute", return_value=fake_result):
        exit_code = main([
            "run",
            "--window-start", "2026-06-07T11:00:00+00:00",
            "--window-end",   "2026-06-07T12:00:00+00:00",
            "--dry-run",
        ])

    assert exit_code == 0


def test_cli_run_dry_run_with_anomalies_exits_two(tmp_path: Path):
    """CLI with anomalies detected and no errors must exit 2."""
    from opsmitra.runtime import RuntimeResult

    fake_result = RuntimeResult(
        anomalies_detected=1,
        anomalies_alerted=1,
        anomalies_suppressed=0,
        source_kind="local",
        sink_kind="stdout",
        dry_run=True,
        errors=[],
    )

    with patch("opsmitra.cli._compose_and_execute", return_value=fake_result):
        exit_code = main([
            "run",
            "--window-start", "2026-06-07T11:00:00+00:00",
            "--window-end",   "2026-06-07T12:00:00+00:00",
            "--dry-run",
        ])

    assert exit_code == 2


def test_cli_run_bad_timestamp_exits_one(capsys):
    """--window-start with a non-ISO timestamp must exit 1 with a friendly error message.

    RED: current stub doesn't have --window-start; once implemented it must
    return exit code 1 (not 2/argparse) for a bad timestamp value, and print
    a friendly message to stderr.
    """
    # Import here so test fails with ModuleNotFoundError if runtime not implemented
    from opsmitra.runtime import RuntimeResult  # noqa: F401 — triggers RED

    exit_code = main([
        "run",
        "--window-start", "NOTATIMESTAMP",
        "--window-end",   "2026-06-07T12:00:00+00:00",
        "--dry-run",
    ])

    assert exit_code == 1
    captured = capsys.readouterr()
    # Error output must be on stderr and mention the bad value or flag
    assert "NOTATIMESTAMP" in captured.err or "window-start" in captured.err.lower()


def test_cli_run_missing_required_args_exits_nonzero():
    """Omitting required arguments (--window-start / --window-end) must exit non-zero.

    RED: current stub's main() returns 0 for bare 'run'; once --window-start and
    --window-end are required arguments, argparse will exit 2 for missing args.
    """
    # Import here so test fails with ModuleNotFoundError if runtime not implemented
    from opsmitra.runtime import RuntimeResult  # noqa: F401 — triggers RED

    with pytest.raises(SystemExit) as exc_info:
        main(["run"])

    # argparse exits 2 for missing required arguments
    assert exc_info.value.code != 0


def test_cli_run_with_errors_exits_one(tmp_path: Path):
    """CLI must exit 1 when RuntimeResult.errors is non-empty."""
    from opsmitra.runtime import RuntimeResult

    fake_result = RuntimeResult(
        anomalies_detected=1,
        anomalies_alerted=0,
        anomalies_suppressed=0,
        source_kind="local",
        sink_kind="stdout",
        dry_run=True,
        errors=["alert_delivery_failed"],
    )

    with patch("opsmitra.cli._compose_and_execute", return_value=fake_result):
        exit_code = main([
            "run",
            "--window-start", "2026-06-07T11:00:00+00:00",
            "--window-end",   "2026-06-07T12:00:00+00:00",
            "--dry-run",
        ])

    assert exit_code == 1


def test_cli_compose_and_execute_raises_runtime_error_exits_one(tmp_path: Path, capsys):
    """When _compose_and_execute raises OpsMitraRuntimeError, main must exit 1."""
    from opsmitra.runtime import EventSourceError

    with patch("opsmitra.cli._compose_and_execute", side_effect=EventSourceError("boom")):
        exit_code = main([
            "run",
            "--window-start", "2026-06-07T11:00:00+00:00",
            "--window-end",   "2026-06-07T12:00:00+00:00",
            "--dry-run",
        ])

    assert exit_code == 1


def test_compose_and_execute_local_source_no_events(tmp_path: Path, monkeypatch):
    """_compose_and_execute with a local source returning no events must return
    a RuntimeResult with zero anomalies detected."""
    import argparse
    from opsmitra.cli import _compose_and_execute

    # Create an empty NDJSON events file
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("")
    cooldown_file = tmp_path / "cooldown.json"

    # Set env vars for local source + dry run + cooldown in tmp_path
    env_overrides = {
        "OPSMITRA_LOG_PATH": str(events_file),
        "OPSMITRA_DRY_RUN": "true",
        "OPSMITRA_EVENT_SOURCE": "local",
        "OPSMITRA_COOLDOWN_PATH": str(cooldown_file),
    }
    monkeypatch.setattr("os.environ", {**__import__("os").environ, **env_overrides})

    args = argparse.Namespace(
        source="local",
        window_start="2026-06-07T11:00:00+00:00",
        window_end="2026-06-07T12:00:00+00:00",
        tenant=None,
        types=None,
        dry_run=True,
        config_file=None,
    )

    result = _compose_and_execute(args)
    assert result.anomalies_detected == 0
    assert result.dry_run is True
    assert result.source_kind == "local"


def test_compose_and_execute_fallback_summarizer_when_provider_not_ollama(tmp_path: Path, monkeypatch):
    """When model provider is not 'ollama', FallbackSummarizer is used."""
    import argparse
    from opsmitra.cli import _compose_and_execute

    events_file = tmp_path / "events.jsonl"
    events_file.write_text("")
    cooldown_file = tmp_path / "cooldown.json"

    env_overrides = {
        "OPSMITRA_LOG_PATH": str(events_file),
        "OPSMITRA_DRY_RUN": "true",
        "OPSMITRA_EVENT_SOURCE": "local",
        "OPSMITRA_MODEL_PROVIDER": "fallback",
        "OPSMITRA_COOLDOWN_PATH": str(cooldown_file),
    }
    monkeypatch.setattr("os.environ", {**__import__("os").environ, **env_overrides})

    args = argparse.Namespace(
        source="local",
        window_start="2026-06-07T11:00:00+00:00",
        window_end="2026-06-07T12:00:00+00:00",
        tenant=None,
        types=None,
        dry_run=True,
        config_file=None,
    )

    result = _compose_and_execute(args)
    assert result.anomalies_detected == 0


def test_cli_run_does_not_print_webhook_url(tmp_path: Path, capsys):
    """The webhook URL sentinel must not appear in stdout or stderr during a run."""
    _WEBHOOK_SENTINEL = "https://hooks.slack.com/services/CLI-SENTINEL-UNIQUE"

    from opsmitra.runtime import RuntimeResult

    fake_result = RuntimeResult(
        anomalies_detected=0,
        anomalies_alerted=0,
        anomalies_suppressed=0,
        source_kind="local",
        sink_kind="stdout",
        dry_run=True,
        errors=[],
    )

    env_override = {"OPSMITRA_SLACK_WEBHOOK_URL": _WEBHOOK_SENTINEL}

    with patch("opsmitra.cli._compose_and_execute", return_value=fake_result):
        with patch.dict("os.environ", env_override):
            main([
                "run",
                "--window-start", "2026-06-07T11:00:00+00:00",
                "--window-end",   "2026-06-07T12:00:00+00:00",
                "--dry-run",
            ])

    captured = capsys.readouterr()
    assert _WEBHOOK_SENTINEL not in captured.out
    assert _WEBHOOK_SENTINEL not in captured.err


def test_python_m_opsmitra_invokes_cli():
    """python -m opsmitra --version must exit 0 and print the version string."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "opsmitra", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    from opsmitra import __version__
    assert __version__ in result.stdout


def test_help_text_documents_exit_codes():
    """parser.format_help() must document exit codes (M2 requirement)."""
    from opsmitra.cli import build_parser
    help_text = build_parser().format_help()
    assert "Exit codes" in help_text
    assert "check stderr" in help_text


# ---------------------------------------------------------------------------
# Step 9 eval subcommand — direct main() call tests (coverage boost)
# ---------------------------------------------------------------------------


def test_cli_eval_default_fixtures_exits_zero_direct(tmp_path: Path):
    """main(['eval', '--dataset', ...]) with real fixtures exits 0."""
    fixtures_dir = Path(__file__).parent / "fixtures" / "evaluation"
    exit_code = main(["eval", "--dataset", str(fixtures_dir)])
    assert exit_code == 0


def test_cli_eval_json_report_exits_zero_direct(capsys, tmp_path: Path):
    """main(['eval', '--report', 'json', ...]) emits valid JSON and exits 0."""
    import json as _json
    fixtures_dir = Path(__file__).parent / "fixtures" / "evaluation"
    exit_code = main(["eval", "--dataset", str(fixtures_dir), "--report", "json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    parsed = _json.loads(captured.out)
    assert "cases" in parsed


def test_cli_eval_output_file_direct(tmp_path: Path):
    """main(['eval', '--output', path]) writes report to file and exits 0."""
    fixtures_dir = Path(__file__).parent / "fixtures" / "evaluation"
    output_path = tmp_path / "report.json"
    exit_code = main([
        "eval",
        "--dataset", str(fixtures_dir),
        "--report", "json",
        "--output", str(output_path),
    ])
    assert exit_code == 0
    assert output_path.exists()


def test_cli_eval_strict_missed_critical_exits_one_direct(tmp_path: Path):
    """main(['eval', '--strict', '--dataset', dir_with_miss]) exits 1."""
    import json as _json
    events_path = tmp_path / "impossible.events.jsonl"
    events_path.write_text("", encoding="utf-8")
    expected = {
        "name": "impossible",
        "description": "Will always miss.",
        "window": {"start": "2026-01-15T06:00:00Z", "end": "2026-01-15T07:00:00Z"},
        "expected_incidents": [
            {
                "type": "cost_runaway_usage",
                "tenant_id": "t_strict",
                "subject": {},
                "severity": "high",
                "first_seen": "2026-01-15T06:10:00Z",
                "must_detect": True,
            }
        ],
        "thresholds": None,
    }
    (tmp_path / "impossible.expected.json").write_text(_json.dumps(expected))
    exit_code = main(["eval", "--dataset", str(tmp_path), "--strict"])
    assert exit_code == 1


def test_cli_eval_missing_dataset_exits_one_direct(tmp_path: Path):
    """main(['eval', '--dataset', nonexistent]) exits 1 with IO error."""
    nonexistent = tmp_path / "does_not_exist"
    exit_code = main(["eval", "--dataset", str(nonexistent)])
    assert exit_code == 1


def test_cli_eval_non_strict_missed_exits_two_direct(tmp_path: Path):
    """main(['eval', '--dataset', dir_with_miss]) without --strict exits 2."""
    import json as _json
    events_path = tmp_path / "impossible2.events.jsonl"
    events_path.write_text("", encoding="utf-8")
    expected = {
        "name": "impossible2",
        "description": "Will always miss.",
        "window": {"start": "2026-01-15T06:00:00Z", "end": "2026-01-15T07:00:00Z"},
        "expected_incidents": [
            {
                "type": "cost_runaway_usage",
                "tenant_id": "t_nonstrict",
                "subject": {},
                "severity": "high",
                "first_seen": "2026-01-15T06:10:00Z",
                "must_detect": True,
            }
        ],
        "thresholds": None,
    }
    (tmp_path / "impossible2.expected.json").write_text(_json.dumps(expected))
    exit_code = main(["eval", "--dataset", str(tmp_path)])
    assert exit_code == 2


# ---------------------------------------------------------------------------
# Step 10 M2 — exit code 64 (EX_USAGE) for argparse misuse
# ---------------------------------------------------------------------------


def test_cli_argparse_misuse_exits_64():
    """Invoking `python -m opsmitra run --invalid-flag` must exit with code 64
    (EX_USAGE / sysexits.h), NOT the default argparse exit code of 2.

    RED until M2 subclasses ArgumentParser with error() → sys.exit(64).
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "opsmitra", "run", "--invalid-flag"],
        capture_output=True,
        text=True,
        check=False,
        env=__import__("os").environ.copy(),
    )

    # RED: current argparse exit code is 2, not 64
    assert result.returncode == 64, (
        f"Expected exit code 64 (EX_USAGE) for argparse misuse, "
        f"got {result.returncode}. stderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# H1 — --config-file threshold override is honored end-to-end
# ---------------------------------------------------------------------------


def test_cli_run_config_file_load_error_propagates(tmp_path: Path, capsys):
    """If load_thresholds raises ValueError (bad path), main() must exit 1."""
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("")
    env_override = {
        "OPSMITRA_LOG_PATH": str(events_path),
        "OPSMITRA_SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/FAKE",
    }
    with patch.dict("os.environ", env_override):
        exit_code = main([
            "run",
            "--window-start", "2026-06-07T11:00:00+00:00",
            "--window-end",   "2026-06-07T12:00:00+00:00",
            "--dry-run",
            "--config-file", str(tmp_path / "nonexistent.json"),
        ])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error" in captured.err.lower() or "config" in captured.err.lower()


def test_cli_eval_config_file_load_error_returns_one(tmp_path: Path, capsys):
    """If load_thresholds raises ValueError in eval, _cmd_eval must return 1."""
    fixtures_dir = Path(__file__).parent / "fixtures" / "evaluation"
    exit_code = main([
        "eval",
        "--dataset", str(fixtures_dir),
        "--config-file", str(tmp_path / "nonexistent.json"),
    ])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error" in captured.err.lower() or "config" in captured.err.lower()


def test_cli_run_config_file_bad_path_exits_one(tmp_path: Path, capsys):
    """CLI 'run --config-file <non-existent-path>' must exit 1 with a helpful message."""
    from opsmitra.runtime import RuntimeResult

    # _compose_and_execute will raise ValueError from load_thresholds on bad path
    import subprocess
    import os as _os

    env = _os.environ.copy()
    env.setdefault("OPSMITRA_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/FAKE-WEBOOK-URL")

    result = subprocess.run(
        [
            sys.executable, "-m", "opsmitra", "run",
            "--window-start", "2026-06-07T11:00:00+00:00",
            "--window-end", "2026-06-07T12:00:00+00:00",
            "--dry-run",
            "--config-file", str(tmp_path / "nonexistent.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 1, (
        f"Expected exit code 1 for bad config-file path, got {result.returncode}. "
        f"stderr: {result.stderr!r}"
    )


def test_cli_eval_config_file_bad_path_exits_one(tmp_path: Path):
    """CLI 'eval --config-file <non-existent-path>' must exit 1 with a helpful message."""
    import subprocess
    import os as _os

    fixtures_dir = Path(__file__).parent / "fixtures" / "evaluation"
    result = subprocess.run(
        [
            sys.executable, "-m", "opsmitra", "eval",
            "--dataset", str(fixtures_dir),
            "--config-file", str(tmp_path / "nonexistent.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_os.environ.copy(),
    )
    assert result.returncode == 1, (
        f"Expected exit code 1 for bad eval config-file path, got {result.returncode}. "
        f"stderr: {result.stderr!r}"
    )


def test_cli_run_config_file_threshold_override_honored(tmp_path: Path):
    """CLI 'run --config-file <path>' must load thresholds from the JSON file and
    pass them into detect_anomalies.

    Scenario (positive — anomaly fires with low threshold):
      - 50 SMS events in a 60-minute window (no baseline → ratio = 50 >> 5.0 OK)
      - config file sets sms_abuse_rate_per_minute=0.5 → sms_min_count=30 for 60min
      - 50 >= 30 and ratio 50 >= 5.0 → sms_abuse_spike anomaly fires → exit code 2

    Scenario (negative — no anomaly with default threshold):
      - Same 50 events; no --config-file → default sms_abuse_rate_per_minute=5.0
      - sms_min_count = 300 for 60-min window; 50 < 300 → no anomaly → exit code 0
    """
    import json as _json
    import os as _os

    # Build a minimal events JSONL file with 50 SMS events in the detection window
    window_start = "2026-06-07T11:00:00Z"
    window_end = "2026-06-07T12:00:00Z"
    events_path = tmp_path / "events.jsonl"
    events = []
    for i in range(50):
        ts = f"2026-06-07T11:{i // 60:02d}:{i % 60:02d}Z"
        events.append({
            "timestamp": ts,
            "tenant_id": "tenant_h1_test",
            "endpoint": "/sms/send",
            "method": "POST",
            "status_code": 200,
            "cost_units": 0.0,
            "request_id": f"req_h1_{i:04d}",
            "api_key_id": "key_h1_test",
        })
    events_path.write_text("\n".join(_json.dumps(e) for e in events), encoding="utf-8")

    # Write thresholds config: very low sms_abuse_rate_per_minute so 50 events trip it
    config_path = tmp_path / "thresholds.json"
    config_path.write_text(_json.dumps({"default": {"sms_abuse_rate_per_minute": 0.5}}), encoding="utf-8")

    env = _os.environ.copy()
    env["OPSMITRA_LOG_PATH"] = str(events_path)
    env.setdefault("OPSMITRA_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/FAKE-WEBOOK-URL")

    # Positive: low threshold → anomaly detected → exit code 2
    result_with_config = __import__("subprocess").run(
        [
            sys.executable, "-m", "opsmitra", "run",
            "--window-start", window_start,
            "--window-end", window_end,
            "--dry-run",
            "--config-file", str(config_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result_with_config.returncode == 2, (
        f"Expected exit code 2 (anomaly detected) with low threshold, "
        f"got {result_with_config.returncode}. "
        f"stdout: {result_with_config.stdout!r}, stderr: {result_with_config.stderr!r}"
    )

    # Negative: default threshold (sms_min_count=300) → no anomaly → exit code 0
    result_no_config = __import__("subprocess").run(
        [
            sys.executable, "-m", "opsmitra", "run",
            "--window-start", window_start,
            "--window-end", window_end,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result_no_config.returncode == 0, (
        f"Expected exit code 0 (no anomaly) with default threshold, "
        f"got {result_no_config.returncode}. "
        f"stdout: {result_no_config.stdout!r}, stderr: {result_no_config.stderr!r}"
    )
