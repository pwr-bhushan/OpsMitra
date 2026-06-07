"""Step 11 preflight: validate every required env var per feature mode.

Modes auto-detect from the environment (e.g. ``OPSMITRA_DRY_RUN=false``
activates ``slack``; ``OPSMITRA_EVENT_SOURCE=athena`` activates
``aws-source``). Per-mode status is ``ok`` / ``warning`` / ``error``; cross-field
invariants are enforced where a required env var is missing for a mode that is
actually in use.

Pure config — no network calls. Secret values (webhook URL) are reported as
``set`` / ``unset`` only and never reach the rendered output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Literal, Mapping

from opsmitra.config import AppConfig, load_config

Status = Literal["ok", "warning", "error"]
Mode = Literal["model", "slack", "aws-source", "aws-sink", "runtime", "eval"]

ALL_MODES: Final[tuple[Mode, ...]] = (
    "model",
    "slack",
    "aws-source",
    "aws-sink",
    "runtime",
    "eval",
)

# Vars whose value must never appear in rendered output. Status is "set"/"unset".
_SECRET_VAR_NAMES: Final[frozenset[str]] = frozenset(
    {
        "OPSMITRA_SLACK_WEBHOOK_URL",
    }
)


@dataclass(frozen=True)
class CheckResult:
    var_name: str
    status: Status
    message: str


@dataclass(frozen=True)
class ModeReport:
    mode: Mode
    active: bool
    status: Status
    checks: tuple[CheckResult, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    modes: tuple[ModeReport, ...]
    overall_status: Status


# ---------- helpers ----------


def _is_set(env: Mapping[str, str], name: str) -> bool:
    return name in env and env[name] != ""


def _ok(var: str, msg: str) -> CheckResult:
    return CheckResult(var, "ok", msg)


def _warning(var: str, msg: str) -> CheckResult:
    return CheckResult(var, "warning", msg)


def _error(var: str, msg: str) -> CheckResult:
    return CheckResult(var, "error", msg)


def _aggregate(checks: tuple[CheckResult, ...]) -> Status:
    if any(c.status == "error" for c in checks):
        return "error"
    if any(c.status == "warning" for c in checks):
        return "warning"
    return "ok"


def _aggregate_overall(modes: tuple[ModeReport, ...]) -> Status:
    if any(m.active and m.status == "error" for m in modes):
        return "error"
    if any(m.active and m.status == "warning" for m in modes):
        return "warning"
    return "ok"


# ---------- per-mode validators ----------


def validate_model(env: Mapping[str, str], config: AppConfig) -> ModeReport:
    provider = env.get("OPSMITRA_MODEL_PROVIDER", config.model.provider)
    active = provider != "fallback"
    checks: list[CheckResult] = [_ok("OPSMITRA_MODEL_PROVIDER", f"provider={provider}")]
    if active:
        for var, value in [
            ("OPSMITRA_MODEL_URL", config.model.endpoint_url),
            ("OPSMITRA_MODEL_NAME", config.model.model_name),
            ("OPSMITRA_MODEL_TIMEOUT", str(config.model.timeout_seconds)),
        ]:
            if _is_set(env, var):
                checks.append(_ok(var, f"={value}"))
            else:
                checks.append(_warning(var, f"unset; using default {value!r}"))
    else:
        checks.append(_ok("OPSMITRA_MODEL_*", "fallback provider; model env unused"))
    return ModeReport("model", active, _aggregate(tuple(checks)), tuple(checks))


def validate_slack(env: Mapping[str, str], config: AppConfig) -> ModeReport:
    dry_run = config.alert.dry_run
    active = not dry_run
    checks: list[CheckResult] = [
        _ok("OPSMITRA_DRY_RUN", f"dry_run={dry_run}"),
    ]
    if active:
        if _is_set(env, "OPSMITRA_SLACK_WEBHOOK_URL"):
            checks.append(_ok("OPSMITRA_SLACK_WEBHOOK_URL", "set (value redacted)"))
        else:
            checks.append(
                _error(
                    "OPSMITRA_SLACK_WEBHOOK_URL",
                    "required when OPSMITRA_DRY_RUN=false; not set",
                )
            )
        # Optional tuning knobs — report values for visibility
        for var, value in [
            (
                "OPSMITRA_SLACK_TIMEOUT_SECONDS",
                str(config.alert.slack_timeout_seconds),
            ),
            ("OPSMITRA_SLACK_MAX_RETRIES", str(config.alert.slack_max_retries)),
            (
                "OPSMITRA_SLACK_BACKOFF_BASE_SECONDS",
                str(config.alert.slack_backoff_base_seconds),
            ),
            (
                "OPSMITRA_SLACK_BACKOFF_MAX_SECONDS",
                str(config.alert.slack_backoff_max_seconds),
            ),
            (
                "OPSMITRA_SLACK_TOTAL_WAIT_CAP_SECONDS",
                str(config.alert.slack_total_wait_cap_seconds),
            ),
        ]:
            checks.append(_ok(var, f"={value}"))
        if _is_set(env, "OPSMITRA_SLACK_CHANNEL_OVERRIDE"):
            checks.append(
                _ok(
                    "OPSMITRA_SLACK_CHANNEL_OVERRIDE",
                    f"={config.alert.slack_channel_override}",
                )
            )
    else:
        checks.append(
            _ok("OPSMITRA_SLACK_*", "dry_run=true; Slack env unused")
        )
    return ModeReport("slack", active, _aggregate(tuple(checks)), tuple(checks))


def validate_aws_source(env: Mapping[str, str], config: AppConfig) -> ModeReport:
    kind = config.event_source_kind
    active = kind == "athena"
    checks: list[CheckResult] = [_ok("OPSMITRA_EVENT_SOURCE", f"={kind}")]
    if active:
        if config.aws.athena_output_location:
            checks.append(
                _ok(
                    "OPSMITRA_ATHENA_OUTPUT_LOCATION",
                    f"={config.aws.athena_output_location}",
                )
            )
        else:
            checks.append(
                _error(
                    "OPSMITRA_ATHENA_OUTPUT_LOCATION",
                    "required when OPSMITRA_EVENT_SOURCE=athena; not set",
                )
            )
        for var, value in [
            ("OPSMITRA_AWS_REGION", config.aws.region),
            ("OPSMITRA_ATHENA_DATABASE", config.aws.athena_database),
            ("OPSMITRA_ATHENA_TABLE", config.aws.athena_table),
            ("OPSMITRA_ATHENA_WORKGROUP", config.aws.athena_workgroup),
        ]:
            checks.append(_ok(var, f"={value}"))
    else:
        checks.append(
            _ok("OPSMITRA_ATHENA_*", "event source is local; Athena env unused")
        )
    return ModeReport("aws-source", active, _aggregate(tuple(checks)), tuple(checks))


def validate_aws_sink(env: Mapping[str, str], config: AppConfig) -> ModeReport:
    kind = config.event_sink_kind
    active = kind == "s3"
    checks: list[CheckResult] = [_ok("OPSMITRA_EVENT_SINK", f"={kind}")]
    if active:
        if config.aws.s3_bucket:
            checks.append(_ok("OPSMITRA_S3_BUCKET", f"={config.aws.s3_bucket}"))
        else:
            checks.append(
                _error(
                    "OPSMITRA_S3_BUCKET",
                    "required when OPSMITRA_EVENT_SINK=s3; not set",
                )
            )
        for var, value in [
            ("OPSMITRA_AWS_REGION", config.aws.region),
            ("OPSMITRA_S3_PREFIX", config.aws.s3_prefix),
        ]:
            checks.append(_ok(var, f"={value}"))
    else:
        checks.append(
            _ok("OPSMITRA_S3_*", "event sink is local; S3 env unused")
        )
    return ModeReport("aws-sink", active, _aggregate(tuple(checks)), tuple(checks))


def validate_runtime(env: Mapping[str, str], config: AppConfig) -> ModeReport:
    rt = config.runtime
    checks: tuple[CheckResult, ...] = (
        _ok("OPSMITRA_COOLDOWN_SECONDS", f"={rt.cooldown_seconds}"),
        _ok("OPSMITRA_COOLDOWN_PATH", f"={rt.cooldown_path}"),
        _ok("OPSMITRA_MAX_EVENTS_PER_WINDOW", f"={rt.max_events_per_window}"),
        _ok("OPSMITRA_LOG_PATH", f"={config.local.log_path}"),
        _ok("OPSMITRA_OUTPUT_PATH", f"={config.local.output_path}"),
    )
    return ModeReport("runtime", True, _aggregate(checks), checks)


def validate_eval(env: Mapping[str, str], config: AppConfig) -> ModeReport:
    if config.thresholds_path is not None:
        thresholds_check: CheckResult = _ok(
            "OPSMITRA_THRESHOLDS_PATH", f"={config.thresholds_path}"
        )
    else:
        thresholds_check = _ok(
            "OPSMITRA_THRESHOLDS_PATH", "unset; using DetectorThresholds defaults"
        )
    checks: tuple[CheckResult, ...] = (thresholds_check,)
    return ModeReport("eval", True, _aggregate(checks), checks)


_VALIDATORS: Final[
    dict[Mode, "callable[[Mapping[str, str], AppConfig], ModeReport]"]
] = {
    "model": validate_model,
    "slack": validate_slack,
    "aws-source": validate_aws_source,
    "aws-sink": validate_aws_sink,
    "runtime": validate_runtime,
    "eval": validate_eval,
}


def validate(
    env: Mapping[str, str] | None = None,
    config: AppConfig | None = None,
    modes: tuple[Mode, ...] = ALL_MODES,
) -> ValidationReport:
    """Validate the given env against each requested mode."""
    if env is None:
        import os

        env = os.environ
    if config is None:
        config = load_config(env=env)
    reports = tuple(_VALIDATORS[mode](env, config) for mode in modes)
    return ValidationReport(reports, _aggregate_overall(reports))


# ---------- formatters ----------


def format_report_json(report: ValidationReport) -> str:
    return json.dumps(_report_to_dict(report), indent=2)


def _report_to_dict(report: ValidationReport) -> dict[str, object]:
    return {
        "overall_status": report.overall_status,
        "modes": [
            {
                "mode": m.mode,
                "active": m.active,
                "status": m.status,
                "checks": [
                    {
                        "var_name": c.var_name,
                        "status": c.status,
                        "message": c.message,
                    }
                    for c in m.checks
                ],
            }
            for m in report.modes
        ],
    }


def format_report_table(report: ValidationReport) -> str:
    lines: list[str] = []
    lines.append(f"overall: {report.overall_status.upper()}")
    lines.append("")
    header = f"{'mode':<14} {'active':<8} {'status':<8} {'details':<60}"
    lines.append(header)
    lines.append("-" * len(header))
    for m in report.modes:
        active_str = "yes" if m.active else "no"
        lines.append(
            f"{m.mode:<14} {active_str:<8} {m.status:<8} "
            f"{_first_problem_or_summary(m):<60}"
        )
    lines.append("")
    lines.append("checks:")
    for m in report.modes:
        for c in m.checks:
            badge = {"ok": "OK ", "warning": "WARN", "error": "ERR "}[c.status]
            lines.append(f"  [{badge}] {m.mode:<12} {c.var_name:<40} {c.message}")
    return "\n".join(lines)


def _first_problem_or_summary(m: ModeReport) -> str:
    for c in m.checks:
        if c.status in ("error", "warning"):
            return f"{c.var_name}: {c.message}"
    if not m.active:
        return "(inactive)"
    return "all checks ok"


def parse_modes_arg(value: str) -> tuple[Mode, ...]:
    """Parse a CLI --mode argument: 'all' or comma-separated mode names."""
    if value == "all":
        return ALL_MODES
    raw = [v.strip() for v in value.split(",") if v.strip()]
    valid: list[Mode] = []
    for name in raw:
        if name not in ALL_MODES:
            raise ValueError(
                f"unknown mode {name!r}; expected one of {', '.join(ALL_MODES)} or 'all'"
            )
        valid.append(name)  # type: ignore[arg-type]
    return tuple(valid)
