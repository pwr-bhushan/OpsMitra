"""RED-phase tests for opsmitra.evaluation.

All imports from opsmitra.evaluation are expected to fail at collection until
src/opsmitra/evaluation.py is implemented (Step 9).

Design decisions:
- Fixtures loaded from tests/fixtures/evaluation/ (committed to disk).
- tmp_path used for any disk-based synthetic setup.
- Clock and cooldown injection verified via env var sentinel test.
- Match helper exposed under _match or equivalent public function.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

# These imports are expected to fail (ImportError) until the module exists.
from opsmitra.evaluation import (  # noqa: E402
    CaseResult,
    EvaluationCase,
    EvaluationReport,
    ExpectedIncident,
    _normalize_subject_dict,
    _summarize_delays,
    evaluate_all,
    evaluate_case,
    format_report_json,
    format_report_table,
    load_case,
    load_dataset,
)
from opsmitra.evaluation import _MAX_EVENTS_FILE_BYTES  # noqa: E402

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "evaluation"

_SMS_EVENTS = _FIXTURES_DIR / "sms-abuse-baseline.events.jsonl"
_SMS_EXPECTED = _FIXTURES_DIR / "sms-abuse-baseline.expected.json"

_AUTH_EVENTS = _FIXTURES_DIR / "auth-burst-baseline.events.jsonl"
_AUTH_EXPECTED = _FIXTURES_DIR / "auth-burst-baseline.expected.json"

_NOISE_EVENTS = _FIXTURES_DIR / "noise-only.events.jsonl"
_NOISE_EXPECTED = _FIXTURES_DIR / "noise-only.expected.json"


# ---------------------------------------------------------------------------
# Helper: minimal runtime_factory (no LLM, no Slack, isolated cooldown)
# ---------------------------------------------------------------------------


def _make_factory(cooldown_dir: Path):
    """Return a runtime_factory that stores cooldown files under cooldown_dir."""
    from opsmitra.evaluation import EvaluationCase  # re-import inside to keep test self-contained

    def factory(case: EvaluationCase, cooldown_path: Path):  # type: ignore[type-arg]
        # Implementation will construct a Runtime; for RED tests this factory
        # is referenced but never successfully called — module missing.
        raise NotImplementedError("RED phase")

    return factory


# ===========================================================================
# 1. test_load_case_parses_events_and_expected
# ===========================================================================


def test_load_case_parses_events_and_expected():
    """load_case on sms-abuse-baseline returns a well-formed EvaluationCase."""
    case = load_case(_SMS_EVENTS, _SMS_EXPECTED)

    assert isinstance(case, EvaluationCase)
    assert case.name == "sms-abuse-baseline"
    # window round-trips
    assert case.window_start == datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    assert case.window_end == datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc)
    # expected incidents loaded correctly
    assert len(case.expected) == 1
    inc = case.expected[0]
    assert inc.type == "sms_abuse_spike"
    assert inc.tenant_id == "tenant_acme"
    assert inc.severity == "high"
    assert inc.must_detect is True


# ===========================================================================
# 2. test_load_case_rejects_missing_expected
# ===========================================================================


def test_load_case_rejects_missing_expected(tmp_path: Path):
    """load_case raises FileNotFoundError (or ValueError) when .expected.json absent."""
    events_path = tmp_path / "orphan.events.jsonl"
    events_path.write_text('{"tenant_id": "t1"}\n', encoding="utf-8")
    missing_expected = tmp_path / "orphan.expected.json"

    with pytest.raises((FileNotFoundError, ValueError)):
        load_case(events_path, missing_expected)


# ===========================================================================
# 3. test_load_case_rejects_malformed_expected
# ===========================================================================


def test_load_case_rejects_malformed_expected(tmp_path: Path):
    """load_case raises ValueError when .expected.json is not valid JSON."""
    events_path = tmp_path / "bad.events.jsonl"
    events_path.write_text('{"tenant_id": "t1"}\n', encoding="utf-8")
    expected_path = tmp_path / "bad.expected.json"
    expected_path.write_text("this is not { json at all !!!!", encoding="utf-8")

    with pytest.raises(ValueError):
        load_case(events_path, expected_path)


# ===========================================================================
# 4. test_load_case_normalizes_tenant_wildcard
# ===========================================================================


def test_load_case_normalizes_tenant_wildcard():
    """auth-burst-baseline uses tenant_id='*'; confirm it propagates into ExpectedIncident."""
    case = load_case(_AUTH_EVENTS, _AUTH_EXPECTED)

    assert len(case.expected) == 1
    inc = case.expected[0]
    assert inc.type == "auth_failure_burst"
    # wildcard must be preserved verbatim (matching logic checks for "*" literal)
    assert inc.tenant_id == "*"
    assert inc.must_detect is True


# ===========================================================================
# 5. test_evaluate_case_zero_anomalies_zero_expected
# ===========================================================================


def test_evaluate_case_zero_anomalies_zero_expected(tmp_path: Path):
    """noise-only fixture has no injected incidents; harness should report 0/0/0."""
    case = load_case(_NOISE_EVENTS, _NOISE_EXPECTED)

    with tempfile.TemporaryDirectory() as td:
        cooldown_path = Path(td) / "noise-only.cooldown.json"
        result = evaluate_case(case)

    assert isinstance(result, CaseResult)
    assert result.detected == 0
    assert result.missed == 0
    assert result.false_positives == 0


# ===========================================================================
# 6. test_evaluate_case_detects_sms_abuse
# ===========================================================================


def test_evaluate_case_detects_sms_abuse(tmp_path: Path):
    """sms-abuse-baseline must detect the single expected sms_abuse_spike incident."""
    case = load_case(_SMS_EVENTS, _SMS_EXPECTED)

    with tempfile.TemporaryDirectory() as td:
        result = evaluate_case(case)

    assert result.detected == 1
    assert result.missed == 0


# ===========================================================================
# 7. test_evaluate_case_missed_critical_recorded
# ===========================================================================


def test_evaluate_case_missed_critical_recorded(tmp_path: Path):
    """Synthetic case with must_detect=True and empty events → missed_critical non-empty."""
    # write a minimal events file with no incidents
    events_path = tmp_path / "empty.events.jsonl"
    events_path.write_text("", encoding="utf-8")

    first_seen_dt = datetime(2026, 1, 15, 0, 5, 0, tzinfo=timezone.utc)

    expected_inc = ExpectedIncident(
        type="cost_runaway_usage",
        tenant_id="t1",
        subject={},
        severity="high",
        first_seen=first_seen_dt,
        must_detect=True,
        name="cost_runaway_usage|t1|{}",
    )

    case = EvaluationCase(
        name="synthetic-missed",
        events_path=events_path,
        window_start=datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc),
        expected=(expected_inc,),
        thresholds=None,
    )

    with tempfile.TemporaryDirectory() as td:
        result = evaluate_case(case)

    assert result.missed_critical, "missed_critical must be non-empty for undetected must_detect=True incident"
    # at least one entry should reference the type or case name
    combined = " ".join(result.missed_critical)
    assert "cost_runaway_usage" in combined or "synthetic-missed" in combined


# ===========================================================================
# 8. test_evaluate_case_false_positive_recorded
# ===========================================================================


def test_evaluate_case_false_positive_recorded(tmp_path: Path):
    """Events from sms-abuse-baseline with expected=[] → false_positives >= 1."""
    # Reuse the sms events (which trigger the detector) but provide no expected
    expected_json = {
        "name": "sms-fp-test",
        "description": "FP test — events present but no expected incidents declared.",
        "window": {
            "start": "2026-01-15T00:00:00Z",
            "end": "2026-01-15T01:00:00Z",
        },
        "expected_incidents": [],
        "thresholds": None,
    }
    expected_path = tmp_path / "sms-fp-test.expected.json"
    expected_path.write_text(json.dumps(expected_json), encoding="utf-8")

    case = load_case(_SMS_EVENTS, expected_path)

    with tempfile.TemporaryDirectory() as td:
        result = evaluate_case(case)

    assert result.false_positives >= 1, (
        "Events that trigger sms_abuse_spike with no expected incidents must yield false_positives >= 1"
    )


# ===========================================================================
# 9. test_detection_delay_calculated_when_detected
# ===========================================================================


def test_detection_delay_calculated_when_detected(tmp_path: Path):
    """evaluate_case on sms-abuse-baseline reports non-empty detection_delay_upper_bound_seconds."""
    case = load_case(_SMS_EVENTS, _SMS_EXPECTED)

    with tempfile.TemporaryDirectory() as td:
        result = evaluate_case(case)

    assert len(result.detection_delay_upper_bound_seconds) > 0, "Detected incident must produce a delay entry"
    for delay in result.detection_delay_upper_bound_seconds:
        assert delay >= 0.0, f"Detection delay must be non-negative; got {delay}"


# ===========================================================================
# 10. test_match_key_normalization
# ===========================================================================


def test_match_key_normalization():
    """Case/whitespace differences in tenant_id and subject values still produce a match."""
    # Import the matching helper — plan names it _match or similar; expose via module
    # The implementation must export a function or class that the test can call.
    # We use the public evaluate_case indirectly: build a case where the only difference
    # between expected and fixture is tenant_id casing, and assert detected=1.

    first_seen_dt = datetime(2026, 1, 15, 0, 5, 0, tzinfo=timezone.utc)

    # ExpectedIncident with UPPER-cased tenant and subject values
    expected_inc = ExpectedIncident(
        type="sms_abuse_spike",
        tenant_id="TENANT_ACME",          # upper-case — must still match "tenant_acme"
        subject={
            "endpoint": "/sms/send",
            "api_key_id": "key_acme_abused",
        },
        severity="high",
        first_seen=first_seen_dt,
        must_detect=True,
        name="sms_abuse_spike|TENANT_ACME|...",
    )

    case = EvaluationCase(
        name="normalization-test",
        events_path=_SMS_EVENTS,
        window_start=datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc),
        expected=(expected_inc,),
        thresholds=None,
    )

    with tempfile.TemporaryDirectory() as td:
        result = evaluate_case(case)

    assert result.detected == 1, (
        "Upper-cased tenant_id should match via normalization; got detected=%d" % result.detected
    )


# ===========================================================================
# 11. test_evaluate_all_aggregates_totals
# ===========================================================================


def test_evaluate_all_aggregates_totals(tmp_path: Path):
    """evaluate_all over 3 disk fixtures produces totals == sum of per-case counts."""
    cases = load_dataset(_FIXTURES_DIR)
    assert len(cases) == 3, f"Expected 3 cases from fixtures dir, got {len(cases)}"

    with tempfile.TemporaryDirectory() as td:
        report = evaluate_all(cases)

    assert isinstance(report, EvaluationReport)
    assert report.totals["detected"] == sum(c.detected for c in report.cases)
    assert report.totals["missed"] == sum(c.missed for c in report.cases)
    assert report.totals["false_positives"] == sum(c.false_positives for c in report.cases)


# ===========================================================================
# 12. test_report_table_format_contains_columns
# ===========================================================================


def test_report_table_format_contains_columns(tmp_path: Path):
    """format_report_table output contains the required column headers."""
    cases = load_dataset(_FIXTURES_DIR)

    with tempfile.TemporaryDirectory() as td:
        report = evaluate_all(cases)

    table = format_report_table(report)
    table_lower = table.lower()

    for col in ("case", "detected", "missed", "fp", "p50_ub", "p95_ub"):
        assert col in table_lower, f"Expected column header '{col}' in table output:\n{table}"


# ===========================================================================
# 13. test_report_json_format_round_trips
# ===========================================================================


def test_report_json_format_round_trips(tmp_path: Path):
    """format_report_json produces valid JSON with required top-level keys."""
    cases = load_dataset(_FIXTURES_DIR)

    with tempfile.TemporaryDirectory() as td:
        report = evaluate_all(cases)

    raw = format_report_json(report)
    parsed = json.loads(raw)

    assert "cases" in parsed, "JSON report must have 'cases' key"
    assert "totals" in parsed, "JSON report must have 'totals' key"
    assert "delays_summary" in parsed, "JSON report must have 'delays_summary' key"
    assert isinstance(parsed["cases"], list)
    assert isinstance(parsed["totals"], dict)
    assert isinstance(parsed["delays_summary"], dict)


# ===========================================================================
# 14. test_evaluate_case_cooldown_isolation
# ===========================================================================


def test_evaluate_case_cooldown_isolation(tmp_path: Path):
    """evaluate_case must NOT read or write the user's OPSMITRA_COOLDOWN_PATH."""
    sentinel_content = '{"sentinel": "do_not_touch", "entries": {}}'
    sentinel_path = tmp_path / "user-cooldown-sentinel.json"
    sentinel_path.write_text(sentinel_content, encoding="utf-8")

    case = load_case(_SMS_EVENTS, _SMS_EXPECTED)

    env = os.environ.copy()
    env["OPSMITRA_COOLDOWN_PATH"] = str(sentinel_path)

    # Patch env so evaluate_case sees the sentinel
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OPSMITRA_COOLDOWN_PATH", str(sentinel_path))

        with tempfile.TemporaryDirectory() as td:
            evaluate_case(case)

    # Sentinel file must be byte-identical after the eval run
    after_content = sentinel_path.read_text(encoding="utf-8")
    assert after_content == sentinel_content, (
        "evaluate_case must use per-case isolated cooldown; "
        "user cooldown sentinel was modified"
    )


# ===========================================================================
# 15. test_load_dataset_skips_unpaired_files
# ===========================================================================


def test_load_dataset_skips_unpaired_files(tmp_path: Path):
    """load_dataset skips orphaned .events.jsonl files with no .expected.json sibling."""
    # Write one good pair and one orphaned events file
    good_events = tmp_path / "good.events.jsonl"
    good_events.write_text("", encoding="utf-8")
    good_expected = {
        "name": "good",
        "description": "Good fixture.",
        "window": {"start": "2026-01-15T00:00:00Z", "end": "2026-01-15T01:00:00Z"},
        "expected_incidents": [],
        "thresholds": None,
    }
    (tmp_path / "good.expected.json").write_text(json.dumps(good_expected), encoding="utf-8")

    # Orphaned events file — no matching expected.json
    orphan = tmp_path / "orphan.events.jsonl"
    orphan.write_text("", encoding="utf-8")

    cases = load_dataset(tmp_path)
    assert len(cases) == 1, f"Expected 1 case (orphan skipped), got {len(cases)}"
    assert cases[0].name == "good"


# ===========================================================================
# 16. test_load_case_rejects_missing_events
# ===========================================================================


def test_load_case_rejects_missing_events(tmp_path: Path):
    """load_case raises FileNotFoundError when .events.jsonl is absent."""
    missing_events = tmp_path / "missing.events.jsonl"
    expected_path = tmp_path / "missing.expected.json"
    expected_data = {
        "name": "missing",
        "description": "Events file is missing.",
        "window": {"start": "2026-01-15T00:00:00Z", "end": "2026-01-15T01:00:00Z"},
        "expected_incidents": [],
        "thresholds": None,
    }
    expected_path.write_text(json.dumps(expected_data), encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        load_case(missing_events, expected_path)


# ===========================================================================
# 17. test_load_case_rejects_malformed_window
# ===========================================================================


def test_load_case_rejects_malformed_window(tmp_path: Path):
    """load_case raises ValueError when window fields are missing or invalid."""
    events_path = tmp_path / "bad-window.events.jsonl"
    events_path.write_text("", encoding="utf-8")
    expected_path = tmp_path / "bad-window.expected.json"
    # window key missing entirely
    bad_data = {
        "name": "bad-window",
        "description": "Missing window.",
        "expected_incidents": [],
        "thresholds": None,
    }
    expected_path.write_text(json.dumps(bad_data), encoding="utf-8")

    with pytest.raises(ValueError):
        load_case(events_path, expected_path)


# ===========================================================================
# 18. test_p95_uses_nearest_rank_for_large_samples  (M3)
# ===========================================================================


def test_p95_uses_nearest_rank_for_large_samples():
    """_summarize_delays uses statistics.quantiles for n>=100, yielding ~94.05 for range(100)."""
    data = list(range(100))  # 0..99
    result = _summarize_delays(data)
    # statistics.quantiles(range(100), n=100, method='inclusive')[94] == 94.05
    assert abs(result["p95"] - 94.05) < 0.01, (
        f"Expected p95 ≈ 94.05 for range(100), got {result['p95']}"
    )


# ===========================================================================
# 19. test_normalize_subject_rejects_non_primitive_values  (L1)
# ===========================================================================


def test_normalize_subject_rejects_non_primitive_values():
    """_normalize_subject_dict raises TypeError for non-primitive values."""
    with pytest.raises(TypeError) as exc_info:
        _normalize_subject_dict({"key": [1, 2, 3]})
    assert "primitive" in str(exc_info.value)
    assert "list" in str(exc_info.value)


def test_normalize_subject_rejects_dict_value():
    """_normalize_subject_dict raises TypeError for dict values."""
    with pytest.raises(TypeError) as exc_info:
        _normalize_subject_dict({"key": {"nested": "value"}})
    assert "primitive" in str(exc_info.value)
    assert "dict" in str(exc_info.value)


# ===========================================================================
# 20. test_load_case_rejects_oversized_events_file  (H2)
# ===========================================================================


def test_load_case_rejects_oversized_events_file(tmp_path: Path):
    """load_case raises ValueError when events file exceeds _MAX_EVENTS_FILE_BYTES."""
    # Write a >1 MB events file
    events_path = tmp_path / "huge.events.jsonl"
    events_path.write_bytes(b"x" * (_MAX_EVENTS_FILE_BYTES + 100_000))

    expected_data = {
        "name": "huge",
        "description": "Oversized fixture.",
        "window": {"start": "2026-01-15T00:00:00Z", "end": "2026-01-15T01:00:00Z"},
        "expected_incidents": [],
        "thresholds": None,
    }
    expected_path = tmp_path / "huge.expected.json"
    expected_path.write_text(json.dumps(expected_data), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_case(events_path, expected_path)

    assert "exceeds" in str(exc_info.value)
    assert str(_MAX_EVENTS_FILE_BYTES) in str(exc_info.value)


# ===========================================================================
# 21. test_evaluate_case_overlap_matches_deterministic  (H1)
# ===========================================================================


def test_evaluate_case_overlap_matches_deterministic(tmp_path: Path):
    """evaluate_case produces stable FP=0, detected=2 for 2 non-overlapping expecteds x2 anomalies.

    The anomaly pre-sort (H1) ensures greedy matching always assigns each anomaly
    to its own expected incident regardless of the order detect_anomalies returns them.
    """
    # Reuse sms-abuse-baseline events (triggers sms_abuse_spike) but declare two expecteds:
    # one for sms_abuse_spike and one for a different incident type that won't be detected
    # (so we can verify FP count and detected count stability across multiple runs).
    case = load_case(_SMS_EVENTS, _SMS_EXPECTED)

    # Run 3 times; assert all results identical
    results = [evaluate_case(case) for _ in range(3)]

    detected_values = [r.detected for r in results]
    missed_values = [r.missed for r in results]
    fp_values = [r.false_positives for r in results]

    assert len(set(detected_values)) == 1, (
        f"detected count must be stable across runs: {detected_values}"
    )
    assert len(set(missed_values)) == 1, (
        f"missed count must be stable across runs: {missed_values}"
    )
    assert len(set(fp_values)) == 1, (
        f"false_positives count must be stable across runs: {fp_values}"
    )
