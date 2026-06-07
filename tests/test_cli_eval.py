"""RED-phase CLI tests for the `opsmitra eval` subcommand (Step 9).

All tests run via subprocess to ensure the CLI entry point is exercised
end-to-end, matching the pattern used in test_cli.py for the `run` subcommand.

Design decisions:
- subprocess([sys.executable, "-m", "opsmitra", "eval", ...]) for full isolation.
- env=os.environ.copy() passed to subprocess so the virtualenv PATH is preserved.
- tmp_path used for any synthetic fixture directories.
- These tests are RED until opsmitra.evaluation is implemented and the CLI
  `eval` subcommand is wired in src/opsmitra/cli.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "evaluation"


def _run_eval(
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `python -m opsmitra eval <args>` and return the CompletedProcess."""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "opsmitra", "eval", *args],
        capture_output=True,
        text=True,
        env=env,
    )


# ===========================================================================
# 1. test_cli_eval_default_dataset_exits_zero
# ===========================================================================


def test_cli_eval_default_dataset_exits_zero():
    """opsmitra eval against the 3 committed fixtures should exit 0 (no critical misses)."""
    proc = _run_eval(["--dataset", str(_FIXTURES_DIR)])

    assert proc.returncode == 0, (
        f"Expected exit 0 (all incidents detected), got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )


# ===========================================================================
# 2. test_cli_eval_strict_missed_critical_exits_one
# ===========================================================================


def test_cli_eval_strict_missed_critical_exits_one(tmp_path: Path):
    """--strict mode must exit 1 when a must_detect=true incident is missed."""
    # Create a synthetic fixture pair: events file is effectively empty
    # (no incidents), but expected declares must_detect=true for cost_runaway_usage.
    events_path = tmp_path / "impossible.events.jsonl"
    events_path.write_text("", encoding="utf-8")  # no events → no anomalies

    expected = {
        "name": "impossible",
        "description": "Fixture designed to miss its required incident.",
        "window": {
            "start": "2026-01-15T06:00:00Z",
            "end": "2026-01-15T07:00:00Z",
        },
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
    expected_path = tmp_path / "impossible.expected.json"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")

    proc = _run_eval(["--dataset", str(tmp_path), "--strict"])

    assert proc.returncode == 1, (
        f"Expected exit 1 (strict + missed critical), got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )


# ===========================================================================
# 3. test_cli_eval_report_json_output_to_stdout
# ===========================================================================


def test_cli_eval_report_json_output_to_stdout():
    """--report json must emit valid JSON with a 'cases' key to stdout."""
    proc = _run_eval(["--dataset", str(_FIXTURES_DIR), "--report", "json"])

    assert proc.returncode == 0, (
        f"Expected exit 0, got {proc.returncode}.\nstderr: {proc.stderr}"
    )

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout is not valid JSON: {exc}\nstdout: {proc.stdout!r}")

    assert "cases" in parsed, (
        f"JSON report must have 'cases' key; got keys: {list(parsed.keys())}"
    )


# ===========================================================================
# 4. test_cli_eval_report_output_to_file
# ===========================================================================


def test_cli_eval_report_output_to_file(tmp_path: Path):
    """--output PATH --report json must write a valid JSON file to the given path."""
    output_path = tmp_path / "eval-report.json"

    proc = _run_eval([
        "--dataset", str(_FIXTURES_DIR),
        "--report", "json",
        "--output", str(output_path),
    ])

    assert proc.returncode == 0, (
        f"Expected exit 0, got {proc.returncode}.\nstderr: {proc.stderr}"
    )
    assert output_path.exists(), f"Output file was not created at {output_path}"

    content = output_path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Output file is not valid JSON: {exc}\ncontent: {content!r}")

    assert "cases" in parsed, (
        f"Output file JSON must have 'cases' key; got keys: {list(parsed.keys())}"
    )


# ===========================================================================
# 5. test_cli_eval_strict_non_critical_miss_exits_zero  (H3)
# ===========================================================================


def test_cli_eval_strict_non_critical_miss_exits_zero(tmp_path: Path):
    """--strict mode exits 0 when only non-critical (must_detect=false) incidents are missed."""
    # Fixture: events file with no events; expected has must_detect=false only
    events_path = tmp_path / "noncritical.events.jsonl"
    events_path.write_text("", encoding="utf-8")

    expected = {
        "name": "noncritical",
        "description": "Non-critical miss fixture — must_detect=false.",
        "window": {
            "start": "2026-01-15T06:00:00Z",
            "end": "2026-01-15T07:00:00Z",
        },
        "expected_incidents": [
            {
                "type": "cost_runaway_usage",
                "tenant_id": "t_noncritical",
                "subject": {},
                "severity": "low",
                "first_seen": "2026-01-15T06:10:00Z",
                "must_detect": False,
            }
        ],
        "thresholds": None,
    }
    expected_path = tmp_path / "noncritical.expected.json"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")

    proc = _run_eval(["--dataset", str(tmp_path), "--strict"])

    assert proc.returncode == 0, (
        f"Expected exit 0 (strict + non-critical miss only), got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
