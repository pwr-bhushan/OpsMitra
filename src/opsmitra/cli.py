"""Command line interface for OpsMitra."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from opsmitra import __version__


class _OpsMitraArgumentParser(argparse.ArgumentParser):
    """ArgumentParser subclass that exits with code 64 (EX_USAGE) on usage errors.

    Exit codes:
        0  — success (or --help / --version)
        1  — runtime / configuration error
        2  — anomalies detected
        64 — argparse usage error (wrong flags, missing required args, etc.)
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        sys.exit(64)  # EX_USAGE (sysexits.h)


def build_parser() -> argparse.ArgumentParser:
    parser = _OpsMitraArgumentParser(
        prog="opsmitra",
        description=(
            "Private AI-assisted log anomaly detector.\n"
            "Exit codes: 0 = clean; 1 = runtime/config error; "
            "2 = anomalies detected; 64 = argparse usage error (check stderr to disambiguate)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run one detector pass.")
    run_parser.add_argument("--source", choices=("local", "athena"), default="local")
    # Legacy flag kept for backwards compatibility with existing tests
    run_parser.add_argument("--window-minutes", type=int, default=60)

    # Step 8 additions
    run_parser.add_argument(
        "--window-start",
        default=None,
        metavar="ISO8601",
        help="Window start timestamp in ISO 8601 format (e.g. 2026-06-07T11:00:00+00:00).",
    )
    run_parser.add_argument(
        "--window-end",
        default=None,
        metavar="ISO8601",
        help="Window end timestamp in ISO 8601 format (e.g. 2026-06-07T12:00:00+00:00).",
    )
    run_parser.add_argument("--tenant", default=None, help="Optional tenant filter.")
    run_parser.add_argument(
        "--types",
        nargs="+",
        default=None,
        metavar="ENDPOINT",
        help="Optional endpoint type filters.",
    )

    dry_run_group = run_parser.add_mutually_exclusive_group()
    dry_run_group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Do not send real Slack alerts (default).",
    )
    dry_run_group.add_argument(
        "--send",
        dest="dry_run",
        action="store_false",
        help="Send real Slack alerts.",
    )

    run_parser.add_argument(
        "--config-file",
        default=None,
        metavar="PATH",
        help="Optional path to thresholds JSON config file.",
    )

    # eval subcommand
    eval_parser = subparsers.add_parser(
        "eval",
        help="Run evaluation harness against fixture datasets.",
    )
    eval_parser.add_argument(
        "--dataset",
        default="tests/fixtures/evaluation",
        metavar="PATH",
        help="Directory of fixture pairs (default: tests/fixtures/evaluation).",
    )
    eval_parser.add_argument(
        "--report",
        choices=("json", "table"),
        default="table",
        help="Report output format: 'table' (default) or 'json'.",
    )
    eval_parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Write report to file instead of stdout.",
    )
    eval_parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Exit 1 only if a must_detect (critical) incident is missed; non-critical misses are ignored under strict mode.",
    )
    eval_parser.add_argument(
        "--config-file",
        default=None,
        metavar="PATH",
        help="Optional path to thresholds JSON config file (same format as 'run' subcommand).",
    )

    # validate subcommand
    validate_parser = subparsers.add_parser(
        "validate",
        help="Preflight env-var validation per feature mode (no network).",
    )
    validate_parser.add_argument(
        "--mode",
        default="all",
        metavar="LIST",
        help=(
            "Comma-separated modes to validate: model, slack, aws-source, aws-sink, "
            "runtime, eval (default: all)."
        ),
    )
    validate_parser.add_argument(
        "--report",
        choices=("json", "table"),
        default="table",
        help="Report output format: 'table' (default) or 'json'.",
    )
    validate_parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help=(
            "Exit 1 if any required-for-active-mode env var is missing or invalid. "
            "Without --strict, errors are reported but exit stays 0."
        ),
    )

    return parser


def _parse_iso_timestamp(value: str, flag_name: str) -> datetime:
    """Parse an ISO 8601 timestamp string to a tz-aware UTC datetime.

    Args:
        value: The timestamp string to parse.
        flag_name: The CLI flag name (for error messages).

    Returns:
        A tz-aware datetime in UTC.

    Raises:
        ValueError: If parsing fails.
    """
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"--{flag_name}: invalid ISO 8601 timestamp: {value!r}. "
            "Expected format: YYYY-MM-DDTHH:MM:SS+HH:MM"
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _compose_and_execute(args: argparse.Namespace) -> "RuntimeResult":  # type: ignore[name-defined]
    """Build and execute the Runtime from parsed CLI arguments.

    Imports are done lazily here so the AWS-heavy event_source module is
    only imported when needed (mirrors Step 7 AWS isolation pattern).

    Args:
        args: Parsed CLI arguments from argparse.

    Returns:
        RuntimeResult from the pipeline execution.
    """
    import os

    from opsmitra.config import load_config
    from opsmitra.cooldown import AnomalyCooldown
    from opsmitra.runtime import Runtime, RuntimeConfigurationError
    from opsmitra.summarizer import FallbackSummarizer, OllamaClient, Summarizer
    from opsmitra.slack_alerter import SlackAlerter

    cfg = load_config(os.environ)

    # Build source
    source_kind = getattr(args, "source", None) or cfg.event_source_kind
    if source_kind == "athena":
        # Lazy import of AWS-heavy module (lessons.md AWS isolation rule)
        from opsmitra.aws.athena_source import AthenaEventSource
        source = AthenaEventSource(cfg.aws)
    else:
        from opsmitra.event_source import LocalNDJSONEventSource
        source = LocalNDJSONEventSource(cfg.local.log_path)

    # Build summarizer
    if cfg.model.provider == "ollama":
        client = OllamaClient(
            model_name=cfg.model.model_name,
            endpoint_url=cfg.model.endpoint_url,
            timeout_seconds=cfg.model.timeout_seconds,
        )
        summarizer = Summarizer(client=client, fallback=FallbackSummarizer())
    else:
        summarizer = FallbackSummarizer()

    # Build alerter
    from opsmitra.config import AlertConfig
    alert_cfg = cfg.alert
    if hasattr(args, "dry_run"):
        # Override dry_run from CLI flag
        alert_cfg = AlertConfig(
            slack_webhook_url=cfg.alert.slack_webhook_url,
            dry_run=args.dry_run,
            slack_timeout_seconds=cfg.alert.slack_timeout_seconds,
            slack_max_retries=cfg.alert.slack_max_retries,
            slack_backoff_base_seconds=cfg.alert.slack_backoff_base_seconds,
            slack_backoff_max_seconds=cfg.alert.slack_backoff_max_seconds,
            slack_total_wait_cap_seconds=cfg.alert.slack_total_wait_cap_seconds,
            slack_channel_override=cfg.alert.slack_channel_override,
        )
    alerter = SlackAlerter(alert_cfg)

    # Build cooldown
    cooldown = AnomalyCooldown(
        cfg.runtime.cooldown_path,
        window_seconds=cfg.runtime.cooldown_seconds,
    )

    # Build thresholds: load from --config-file if provided, then resolve
    # per-tenant / per-endpoint. Falls back to DetectorThresholds() defaults
    # when no --config-file is given.
    from opsmitra.config import DetectorThresholds, load_thresholds, resolve_thresholds
    config_file_path = getattr(args, "config_file", None)
    if config_file_path:
        try:
            overrides = load_thresholds(config_file_path)
        except (ValueError, OSError) as exc:
            print(f"Error loading thresholds config: {exc}", file=sys.stderr)
            raise
        thresholds = resolve_thresholds(
            overrides,
            tenant=getattr(args, "tenant", None),
            endpoint=(getattr(args, "types", None) or [None])[0],
        )
    else:
        thresholds = DetectorThresholds()

    # Build and execute runtime
    dry_run = getattr(args, "dry_run", True)
    sink_kind = "stdout" if dry_run else "slack"

    runtime = Runtime(
        source=source,
        summarizer=summarizer,
        alerter=alerter,
        cooldown=cooldown,
        thresholds=thresholds,
        dry_run=dry_run,
        source_kind=source_kind,
        sink_kind=sink_kind,
        max_events_per_window=cfg.runtime.max_events_per_window,
    )

    window_start = getattr(args, "window_start_dt", None) or _parse_iso_timestamp(args.window_start, "window-start")
    window_end = getattr(args, "window_end_dt", None) or _parse_iso_timestamp(args.window_end, "window-end")

    return runtime.execute(
        window_start=window_start,
        window_end=window_end,
        tenant=getattr(args, "tenant", None),
        types=getattr(args, "types", None),
    )


def _result_to_exit_code(result: "RuntimeResult") -> int:  # type: ignore[name-defined]
    """Map RuntimeResult to CLI exit code.

    Mapping:
        0 — succeeded, zero anomalies detected
        2 — succeeded, ≥1 anomaly detected
        1 — errors present (partial failure)
    """
    if result.errors:
        return 1
    if result.anomalies_detected > 0:
        return 2
    return 0


def _print_result(result: "RuntimeResult") -> None:  # type: ignore[name-defined]
    """Pretty-print a RuntimeResult as a JSON summary to stdout.

    Prints counts only — never URLs, event data, or raw error details.
    """
    summary = {
        "anomalies_detected": result.anomalies_detected,
        "anomalies_alerted": result.anomalies_alerted,
        "anomalies_suppressed": result.anomalies_suppressed,
        "source_kind": result.source_kind,
        "sink_kind": result.sink_kind,
        "dry_run": result.dry_run,
        "error_count": len(result.errors),
    }
    print(json.dumps(summary))


def _cmd_eval(args: argparse.Namespace) -> int:
    """Handle the `eval` subcommand.

    Exit codes:
        0 — all expected incidents detected (FPs may exist, with stderr warning).
        1 — strict mode + missed must_detect=true incident, OR I/O error.
        2 — non-strict mode + any missed incident.
    """
    from pathlib import Path as _Path

    from opsmitra.evaluation import (
        evaluate_all,
        format_report_json,
        format_report_table,
        load_dataset,
    )

    dataset_dir = _Path(args.dataset)
    try:
        cases = load_dataset(dataset_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error loading dataset: {exc}", file=sys.stderr)
        return 1

    # Resolve thresholds from --config-file if provided (parity with 'run' subcommand)
    from opsmitra.config import DetectorThresholds, load_thresholds, resolve_thresholds
    config_file_path = getattr(args, "config_file", None)
    if config_file_path:
        try:
            overrides = load_thresholds(config_file_path)
        except (ValueError, OSError) as exc:
            print(f"Error loading thresholds config: {exc}", file=sys.stderr)
            return 1
        eval_thresholds = resolve_thresholds(overrides, tenant=None, endpoint=None)
    else:
        eval_thresholds = DetectorThresholds()

    try:
        report = evaluate_all(cases, thresholds=eval_thresholds)
    except Exception as exc:
        print(f"Evaluation error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    rendered = format_report_json(report) if args.report == "json" else format_report_table(report)

    if args.output:
        try:
            _Path(args.output).write_text(rendered, encoding="utf-8")
        except OSError as exc:
            print(f"Error writing output file: {exc}", file=sys.stderr)
            return 1
    else:
        print(rendered)

    # Exit code logic
    critical_missed = sum(len(c.missed_critical) for c in report.cases)
    any_missed = report.totals.get("missed", 0) > 0

    if args.strict:
        return 1 if critical_missed > 0 else 0
    return 2 if any_missed else 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Run the env validation preflight and emit a per-mode report."""
    from opsmitra.validation import (
        format_report_json,
        format_report_table,
        parse_modes_arg,
        validate,
    )

    try:
        modes = parse_modes_arg(args.mode)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 64  # EX_USAGE, matches argparse misuse semantics

    try:
        report = validate(modes=modes)
    except ValueError as exc:
        # Config bounds errors surface here with the same message they'd give
        # at runtime — exposing them at preflight is the whole point.
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rendered = (
        format_report_json(report) if args.report == "json" else format_report_table(report)
    )
    print(rendered)

    if args.strict and report.overall_status == "error":
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        return _cmd_validate(args)

    if args.command == "eval":
        return _cmd_eval(args)

    if args.command == "run":
        # Check required timestamp args (defined as optional in parser for
        # backwards compatibility with existing tests; enforced here at runtime)
        if args.window_start is None or args.window_end is None:
            # Use argparse error mechanism so we get exit code 2 (standard for
            # missing required args) and a friendly usage message.
            parser.error("the following arguments are required: --window-start, --window-end")

        # Parse timestamps first to give a friendly error before any I/O
        try:
            args.window_start_dt = _parse_iso_timestamp(args.window_start, "window-start")
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        try:
            args.window_end_dt = _parse_iso_timestamp(args.window_end, "window-end")
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        try:
            result = _compose_and_execute(args)
        except Exception as exc:
            from opsmitra.runtime import OpsMitraRuntimeError
            if isinstance(exc, OpsMitraRuntimeError):
                print(f"Runtime error: {type(exc).__name__}", file=sys.stderr)
            elif isinstance(exc, (ValueError, OSError)):
                print(f"Configuration error: {type(exc).__name__}", file=sys.stderr)
            else:
                print(f"Unexpected error: {type(exc).__name__}", file=sys.stderr)
            return 1

        _print_result(result)
        return _result_to_exit_code(result)

    return 0
