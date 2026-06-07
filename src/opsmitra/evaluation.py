"""Evaluation harness for replaying detector pipeline against fixture datasets.

Runs detect_anomalies against committed NDJSON event fixtures and measures
detection accuracy (detected, missed, false_positives, detection_delay).
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_MAX_EVENTS_FILE_BYTES = 1_000_000  # 1 MB cap; mirrors _MAX_THRESHOLDS_FILE_BYTES in config.py


# ---------------------------------------------------------------------------
# Dataclasses (all frozen=True)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpectedIncident:
    type: str
    tenant_id: str | None
    subject: dict[str, Any]
    severity: str
    first_seen: datetime
    must_detect: bool
    name: str  # synthesized key for missed_critical reporting


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    events_path: Path
    window_start: datetime
    window_end: datetime
    expected: tuple[ExpectedIncident, ...]
    thresholds: Any  # DetectorThresholds | None; using Any to avoid heavy import


@dataclass(frozen=True)
class CaseResult:
    case_name: str
    detected: int
    missed: int
    false_positives: int
    detection_delay_upper_bound_seconds: tuple[float, ...]
    missed_critical: tuple[str, ...]  # ExpectedIncident.name values


@dataclass(frozen=True)
class EvaluationReport:
    cases: tuple[CaseResult, ...]
    totals: dict[str, int]          # {"detected", "missed", "false_positives"}
    delays_summary: dict[str, float]  # {"p50", "p95", "max"}


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


def _parse_iso_datetime(value: str) -> datetime:
    """Parse ISO 8601 datetime string (handles trailing Z)."""
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _synth_incident_name(type_: str, tenant_id: str | None, subject: dict[str, Any]) -> str:
    """Create a deterministic name string for an ExpectedIncident."""
    subj_str = json.dumps(subject, sort_keys=True, separators=(",", ":"))
    return f"{type_}|{tenant_id or '*'}|{subj_str}"


# ---------------------------------------------------------------------------
# load_case / load_dataset
# ---------------------------------------------------------------------------


def load_case(events_path: Path, expected_path: Path) -> EvaluationCase:
    """Load one fixture pair and return a fully-populated EvaluationCase.

    Raises:
        FileNotFoundError: if events_path or expected_path does not exist.
        ValueError: if expected_path contains malformed JSON or missing required fields.
    """
    # Validate events file exists
    if not events_path.exists():
        raise FileNotFoundError(f"Events file not found: {events_path}")

    # Enforce size cap before reading
    file_size = events_path.stat().st_size
    if file_size > _MAX_EVENTS_FILE_BYTES:
        raise ValueError(
            "evaluation events file %s exceeds %d bytes" % (events_path, _MAX_EVENTS_FILE_BYTES)
        )

    # Load + validate expected JSON
    if not expected_path.exists():
        raise FileNotFoundError(f"Expected JSON not found: {expected_path}")

    try:
        raw = expected_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed expected.json: {expected_path}") from exc

    # Parse window
    try:
        window_start = _parse_iso_datetime(data["window"]["start"])
        window_end = _parse_iso_datetime(data["window"]["end"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"malformed window in {expected_path}: {exc}") from exc

    # Parse expected incidents
    raw_incidents = data.get("expected_incidents", [])
    incidents: list[ExpectedIncident] = []
    for raw_inc in raw_incidents:
        type_ = str(raw_inc["type"])
        tenant_id = raw_inc.get("tenant_id")
        subject = raw_inc.get("subject") or {}
        severity = str(raw_inc.get("severity", "high"))
        first_seen = _parse_iso_datetime(str(raw_inc["first_seen"]))
        must_detect = bool(raw_inc.get("must_detect", False))
        name = _synth_incident_name(type_, tenant_id, subject)
        incidents.append(
            ExpectedIncident(
                type=type_,
                tenant_id=tenant_id,
                subject=subject,
                severity=severity,
                first_seen=first_seen,
                must_detect=must_detect,
                name=name,
            )
        )

    return EvaluationCase(
        name=str(data.get("name", events_path.stem.replace(".events", ""))),
        events_path=events_path,
        window_start=window_start,
        window_end=window_end,
        expected=tuple(incidents),
        thresholds=data.get("thresholds"),
    )


def load_dataset(dataset_dir: Path) -> tuple[EvaluationCase, ...]:
    """Discover all *.events.jsonl files in dataset_dir and load paired *.expected.json.

    Files without a matching .expected.json sibling are skipped with a warning.
    Raises ValueError if the directory does not exist.
    """
    import logging
    logger = logging.getLogger(__name__)

    if not dataset_dir.exists():
        raise FileNotFoundError(f"Dataset directory not found: {dataset_dir}")

    cases: list[EvaluationCase] = []
    for events_path in sorted(dataset_dir.glob("*.events.jsonl")):
        stem = events_path.name[: -len(".events.jsonl")]
        expected_path = dataset_dir / f"{stem}.expected.json"
        if not expected_path.exists():
            logger.warning("Skipping orphaned events file (no .expected.json): %s", events_path)
            continue
        cases.append(load_case(events_path, expected_path))

    return tuple(cases)


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------


def _normalize_str(s: str | None) -> str:
    return (s or "").strip().lower()


def _normalize_subject_dict(subject: dict[str, Any] | None) -> dict[str, str]:
    """Return a dict of normalized (key, value) pairs.

    Raises:
        TypeError: if any value is not a JSON-serializable primitive
            (str, int, float, bool, or None).
    """
    if not subject:
        return {}
    for k, v in subject.items():
        if not isinstance(v, (str, int, float, bool, type(None))):
            raise TypeError(
                "subject values must be JSON-serializable primitives; got %s"
                % type(v).__name__
            )
    return {_normalize_str(k): _normalize_str(str(v)) for k, v in subject.items()}


def _matches(expected: ExpectedIncident, anomaly: Any) -> bool:
    """Return True if anomaly satisfies the expected incident matching criteria.

    Matching rules:
    - type must match (case-normalized).
    - tenant_id: if expected is "*" → any tenant matches; else case-normalized equality.
    - subject: every key in expected.subject must appear with equal normalized value
      in anomaly.subject (subset match — detector-added keys are ignored).
    """
    from opsmitra.models import Anomaly

    if not isinstance(anomaly, Anomaly):
        return False

    # Type check (case-normalized)
    if _normalize_str(expected.type) != _normalize_str(anomaly.type):
        return False

    # Tenant check
    if expected.tenant_id != "*":
        if _normalize_str(expected.tenant_id) != _normalize_str(anomaly.tenant_id):
            return False

    # Subject subset match
    expected_norm = _normalize_subject_dict(expected.subject)
    anomaly_norm = _normalize_subject_dict(anomaly.subject)
    for key, val in expected_norm.items():
        if anomaly_norm.get(key) != val:
            return False

    return True


# ---------------------------------------------------------------------------
# evaluate_case / evaluate_all
# ---------------------------------------------------------------------------


def evaluate_case(
    case: EvaluationCase,
    *,
    detection_config: Any = None,
    thresholds: Any = None,
) -> CaseResult:
    """Run the detector pipeline on the case events and match against expected incidents.

    Bypasses Runtime entirely — calls detect_anomalies directly to get the actual
    Anomaly list (Runtime only returns counts, not the objects).

    Cooldown isolation: no cooldown is used here; evaluate_case does NOT touch
    OPSMITRA_COOLDOWN_PATH or any cooldown storage.

    Matching: Anomalies are pre-sorted by (type, tenant_id, subject-json) before
    bipartite match. Greedy in expected iteration order: first-matched expected
    consumes the anomaly. This ensures stable, reproducible FP counts across runs.

    Args:
        case: EvaluationCase describing the fixture to evaluate.
        detection_config: Optional DetectionConfig override.

    Returns:
        CaseResult with per-case detection metrics.
    """
    from opsmitra.detectors import DetectionConfig, detect_anomalies
    from opsmitra.event_source import LocalNDJSONEventSource

    cfg = detection_config
    if cfg is None:
        cfg = DetectionConfig()

    # Load all events from the fixture (no window filter in source — pass all, let
    # detect_anomalies split baseline vs current by window_start)
    source = LocalNDJSONEventSource(case.events_path)

    # Fetch full file range: use far-past start to include baseline events
    far_past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    far_future = datetime(2100, 1, 1, tzinfo=timezone.utc)
    all_events = list(source.fetch_events(far_past, far_future))

    anomalies = detect_anomalies(
        all_events,
        window_start=case.window_start,
        window_end=case.window_end,
        config=cfg,
        thresholds=thresholds,
    )

    # TODO(step-10-hardening): switch to streaming if fixture sizes grow above 1 MB cap.

    # Pre-sort anomalies deterministically by (type, tenant_id, subject-json) for
    # stable, reproducible bipartite matching across Python runs and execution orders.
    anomalies = sorted(
        anomalies,
        key=lambda a: (
            (a.type or ""),
            (a.tenant_id or ""),
            json.dumps(a.subject, sort_keys=True, default=str),
        ),
    )

    # --- Bipartite matching (greedy in expected order) ---
    # Greedy in expected iteration order; first-matched expected consumes the anomaly.
    # Anomalies are pre-sorted (see H1) for determinism.
    matched_anomaly_indices: set[int] = set()
    detected = 0
    missed = 0
    missed_critical: list[str] = []
    detection_delays: list[float] = []

    for expected_inc in case.expected:
        found_idx = None
        for idx, anomaly in enumerate(anomalies):
            if idx in matched_anomaly_indices:
                continue
            if _matches(expected_inc, anomaly):
                found_idx = idx
                break

        if found_idx is not None:
            matched_anomaly_indices.add(found_idx)
            detected += 1
            # Detection delay: (window_end - first_seen).total_seconds() clamped to >=0
            delay = max(
                0.0,
                (case.window_end - expected_inc.first_seen).total_seconds(),
            )
            detection_delays.append(delay)
        else:
            missed += 1
            if expected_inc.must_detect:
                missed_critical.append(expected_inc.name)

    false_positives = len(anomalies) - len(matched_anomaly_indices)

    return CaseResult(
        case_name=case.name,
        detected=detected,
        missed=missed,
        false_positives=false_positives,
        detection_delay_upper_bound_seconds=tuple(detection_delays),
        missed_critical=tuple(missed_critical),
    )


def _summarize_delays(data: list[float]) -> dict[str, float]:
    """Compute p50/p95/max statistics over a list of detection-delay values.

    For n >= 100, uses ``statistics.quantiles(data, n=100, method='inclusive')[94]``
    for p95 (index 94 = 95th percentile in a 100-quantile split).

    For n < 100, uses nearest-rank-floor empirical formula
    ``data[int(0.95 * (n-1))]`` after sorting. This is interpolation-free and
    acceptable for small evaluation fixture sample sizes.
    """
    if not data:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0}
    sorted_data = sorted(data)
    n = len(sorted_data)
    p50 = statistics.median(sorted_data)
    if n >= 100:
        p95 = statistics.quantiles(sorted_data, n=100, method="inclusive")[94]
    else:
        # Empirical nearest-rank-floor for small samples
        p95 = sorted_data[int(0.95 * (n - 1))]
    max_delay = sorted_data[-1]
    return {"p50": p50, "p95": p95, "max": max_delay}


def evaluate_all(
    cases: Iterable[EvaluationCase],
    *,
    detection_config: Any = None,
    thresholds: Any = None,
) -> EvaluationReport:
    """Run evaluate_case for every case and aggregate results.

    Args:
        cases: Iterable of EvaluationCase objects.
        detection_config: Optional DetectionConfig override.

    Returns:
        EvaluationReport with per-case results and aggregate totals/delays_summary.
    """
    results: list[CaseResult] = []
    for case in cases:
        result = evaluate_case(
            case,
            detection_config=detection_config,
            thresholds=thresholds,
        )
        results.append(result)

    total_detected = sum(r.detected for r in results)
    total_missed = sum(r.missed for r in results)
    total_fp = sum(r.false_positives for r in results)

    all_delays: list[float] = []
    for r in results:
        all_delays.extend(r.detection_delay_upper_bound_seconds)

    delays_summary = _summarize_delays(all_delays)

    return EvaluationReport(
        cases=tuple(results),
        totals={"detected": total_detected, "missed": total_missed, "false_positives": total_fp},
        delays_summary=delays_summary,
    )


# ---------------------------------------------------------------------------
# Report formatters
# ---------------------------------------------------------------------------


def format_report_table(report: EvaluationReport) -> str:
    """Format the evaluation report as an ASCII table.

    Table includes columns: case, detected, missed, fp, p50_ub, p95_ub.
    """
    rows: list[tuple[str, str, str, str, str, str]] = []
    for result in report.cases:
        per_case_summary = _summarize_delays(list(result.detection_delay_upper_bound_seconds))
        p50 = per_case_summary["p50"]
        p95 = per_case_summary["p95"]
        rows.append((
            result.case_name,
            str(result.detected),
            str(result.missed),
            str(result.false_positives),
            f"{p50:.1f}",
            f"{p95:.1f}",
        ))

    # Totals row
    total_det = report.totals.get("detected", 0)
    total_mis = report.totals.get("missed", 0)
    total_fp = report.totals.get("false_positives", 0)
    overall_p50 = report.delays_summary.get("p50", 0.0)
    overall_p95 = report.delays_summary.get("p95", 0.0)
    rows.append((
        "TOTAL",
        str(total_det),
        str(total_mis),
        str(total_fp),
        f"{overall_p50:.1f}",
        f"{overall_p95:.1f}",
    ))

    # Compute column widths
    headers = ("case", "detected", "missed", "fp", "p50_ub", "p95_ub")
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def fmt_row(cells: tuple[str, ...]) -> str:
        parts = [cells[i].ljust(col_widths[i]) for i in range(len(cells))]
        return "| " + " | ".join(parts) + " |"

    separator = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_row = fmt_row(headers)
    lines = [separator, header_row, separator]
    for i, row in enumerate(rows):
        lines.append(fmt_row(row))
        if i == len(rows) - 2:
            # separator before totals row
            lines.append(separator)
    lines.append(separator)

    return "\n".join(lines)


def _report_to_dict(report: EvaluationReport) -> dict[str, Any]:
    """Convert EvaluationReport to a JSON-serialisable dict."""
    cases_list = []
    for result in report.cases:
        cases_list.append({
            "case_name": result.case_name,
            "detected": result.detected,
            "missed": result.missed,
            "false_positives": result.false_positives,
            "detection_delay_upper_bound_seconds": list(result.detection_delay_upper_bound_seconds),
            "missed_critical": list(result.missed_critical),
        })
    return {
        "cases": cases_list,
        "totals": dict(report.totals),
        "delays_summary": dict(report.delays_summary),
    }


def format_report_json(report: EvaluationReport) -> str:
    """Format the evaluation report as JSON.

    Returns:
        JSON string with 'cases', 'totals', and 'delays_summary' keys.
    """
    return json.dumps(_report_to_dict(report), default=str, indent=2)
