# Step 9: Evaluation Harness — Plan

> Source-of-truth plan for OpsMitra Build Plan Step 9. Mirrors the format of
> `step7-aws-s3-athena.md` and `step8-scheduler-runtime.md`.

## 1. Requirements Restatement

Build a replay-style evaluation harness that runs the full OpsMitra detection
pipeline against committed fixture datasets and produces a metrics report.

**Verbatim acceptance criteria (from build plan lines 86–94):**

1. Evaluation command runs against fixture datasets.
2. Report includes detected, missed, false positive counts, and detection delay.
3. Tests fail when known critical incidents are missed.

### Testable Subgoals

| # | Subgoal | Verification |
|---|---------|--------------|
| SG1 | `opsmitra eval` runs against a default fixture dataset directory | CLI test invokes subcommand, exits 0 |
| SG2 | Each fixture is a `<name>.events.jsonl` + `<name>.expected.json` pair | `load_case` unit tests round-trip a sample |
| SG3 | Report contains `detected`, `missed`, `false_positives`, and `detection_delay` per case | Report formatter tests assert columns/keys |
| SG4 | Missed `must_detect: true` incidents fail `--strict` mode with exit code 1 | CLI test with a deliberately-missing critical incident |
| SG5 | Detector pipeline used is the same orchestrator (`detect_anomalies`) the Runtime uses | `evaluate_case` runs through `Runtime` in dry-run mode |
| SG6 | Fixture seeding is deterministic and reproducible | Helper script regenerates byte-identical files |
| SG7 | Cooldown is isolated per case (no leaking to user's real cooldown file) | Each case uses `tmp_path` cooldown JSON |

---

## 2. CLI Surface

```
opsmitra eval [--dataset PATH] [--report json|table] [--output PATH] [--strict]
```

| Flag | Default | Behavior |
|------|---------|----------|
| `--dataset PATH` | `tests/fixtures/evaluation/` | Directory of fixture pairs |
| `--report FMT` | `table` | `table` (human-readable) or `json` |
| `--output PATH` | stdout | Where to write the report |
| `--strict` | `false` | Exit non-zero on any missed `must_detect: true` incident |

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Clean: no misses, or strict mode with no `must_detect` misses |
| `1` | Strict mode + missed `must_detect=true` (critical) incident, OR runtime/IO error |
| `2` | Non-strict mode + any miss (soft failure suitable for CI gating) |

The 0/1/2 split lets CI choose between "fail-fast on critical regressions" (`--strict`)
and "warn on any miss" (default). **Strict mode ignores non-critical (`must_detect=false`)
misses** — it exits 1 only when a `must_detect=true` incident is missed.
Note: this is intentionally **different** from the Step 8 Runtime exit code semantics
(where `2` = anomalies detected); the `eval` subcommand has its own argparse-level handler.

---

## 3. Fixture Dataset Format

Each fixture is two files in the dataset directory, sharing a stem:

```
tests/fixtures/evaluation/
├── sms-abuse-baseline.events.jsonl       # NDJSON, one Event per line
├── sms-abuse-baseline.expected.json      # ground-truth declaration
├── auth-burst-baseline.events.jsonl
├── auth-burst-baseline.expected.json
├── noise-only.events.jsonl
└── noise-only.expected.json
```

### `<name>.events.jsonl`

NDJSON (one JSON object per line) with the exact schema produced by
`generator.write_jsonl` (which calls `Event.to_dict`). No deviation — fixtures
are generated from the seeded `SyntheticLogGenerator`.

### `<name>.expected.json`

```json
{
  "name": "sms-abuse-baseline",
  "description": "Single SMS abuse incident on tenant_acme over a 60-minute window.",
  "window": {
    "start": "2026-01-15T00:00:00Z",
    "end":   "2026-01-15T01:00:00Z"
  },
  "expected_incidents": [
    {
      "type": "sms_abuse_spike",
      "tenant_id": "tenant_acme",
      "subject": {"api_key_id": "key_acme_abused", "endpoint": "/sms/send"},
      "severity": "high",
      "first_seen": "2026-01-15T00:05:00Z",
      "must_detect": true
    }
  ],
  "thresholds": null
}
```

Field semantics:

- `window.start` / `window.end`: passed to `Runtime.execute(window_start, window_end)`.
- `expected_incidents[*].type`: must match the `Anomaly.type` strings emitted by detectors (e.g. `sms_abuse_spike`, `auth_failure_burst`, `endpoint_error_rate_spike`, `cost_runaway_usage`).
- `expected_incidents[*].subject`: dict whose entries must be a subset of `Anomaly.subject` for a match (subset, not equal — handles detector-added keys like `provider`).
- `expected_incidents[*].first_seen`: ISO datetime; used to compute detection delay against `window_end` (representing the earliest moment the detector could fire).
- `expected_incidents[*].must_detect`: critical incidents that fail `--strict` mode on miss.
- `thresholds`: optional `DetectorThresholds`-shaped dict to override per-case (`null` → defaults).

---

## 4. Metrics Per Fixture

For each case, after running `evaluate_case`:

- **detected**: count of `expected_incidents` matched by output `Anomaly` objects.
- **missed**: `len(expected_incidents) - detected` (per-case).
- **missed_critical**: tuple of expected-incident names where `must_detect=true` and the incident was not matched.
- **false_positives**: count of output `Anomaly` objects NOT matched by any expected incident.
- **detection_delay_upper_bound_seconds**: for each matched incident, `(window_end - first_seen).total_seconds()` (anomalies are window-aggregated, so detection delay is bounded by window granularity; this is an upper bound on true detection latency).

### Aggregate Summary (across all cases)

- `totals = {"detected": int, "missed": int, "false_positives": int}` (sum across cases).
- `delays_summary = {"p50": float, "p95": float, "max": float}` (over all `detection_delay_upper_bound_seconds` values).

---

## 5. New Module: `src/opsmitra/evaluation.py`

```python
@dataclass(frozen=True)
class ExpectedIncident:
    type: str
    tenant_id: str | None
    subject: dict[str, Any]
    severity: str
    first_seen: datetime
    must_detect: bool
    name: str  # synthesized: "{type}|{tenant_id}|{subject_norm}" for missed_critical reporting

@dataclass(frozen=True)
class EvaluationCase:
    name: str
    events_path: Path
    window_start: datetime
    window_end: datetime
    expected: tuple[ExpectedIncident, ...]
    thresholds: DetectorThresholds | None  # None → use defaults

@dataclass(frozen=True)
class CaseResult:
    case_name: str
    detected: int
    missed: int
    false_positives: int
    detection_delay_upper_bound_seconds: tuple[float, ...]  # upper bound: (window_end - first_seen)
    missed_critical: tuple[str, ...]  # ExpectedIncident.name values

@dataclass(frozen=True)
class EvaluationReport:
    cases: tuple[CaseResult, ...]
    totals: dict[str, int]          # {"detected", "missed", "false_positives"}
    delays_summary: dict[str, float]  # {"p50", "p95", "max"}

# --- Public functions ---

def load_case(events_path: Path, expected_path: Path) -> EvaluationCase: ...
def load_dataset(dataset_dir: Path) -> tuple[EvaluationCase, ...]: ...
def evaluate_case(
    case: EvaluationCase,
    *,
    detection_config: Any = None,
) -> CaseResult: ...
def evaluate_all(
    cases: Iterable[EvaluationCase],
    *,
    detection_config: Any = None,
) -> EvaluationReport: ...
def format_report_table(report: EvaluationReport) -> str: ...
def format_report_json(report: EvaluationReport) -> str: ...
```

### Matching Key

`match_key(type, tenant_id, subject) → tuple[str, str, str]`:

1. `type_norm = type.strip().lower()`
2. `tenant_norm = (tenant_id or "*").strip().lower()`
3. `subject_norm`: deterministic JSON serialization of subject with sorted keys, lowercase string values, whitespace stripped. Concretely: `json.dumps(_normalize_subject(subject), sort_keys=True, separators=(",", ":"))`.

For a detected `Anomaly` to count as matching an `ExpectedIncident`:

- `type_norm` equal AND
- `tenant_norm` equal (with `"*"` wildcard on expected side meaning "any tenant") AND
- Every key in `expected.subject` (normalized) appears with equal value in `anomaly.subject` (normalized). **Subset match**, not equal — so detector-added keys (e.g. `provider`) don't break matching.

Each `ExpectedIncident` and each `Anomaly` is matched at most once (greedy in
expected order). This gives us deterministic `detected` and `false_positives`
counts even when one expected incident could overlap multiple anomalies.

### Evaluation Approach

`evaluate_case` bypasses Runtime entirely and calls `detect_anomalies` directly
after loading events through `LocalNDJSONEventSource`. This gives the exact
`Anomaly` list needed for bipartite matching without needing to wire a capturing
alerter through Runtime. No `runtime_factory` parameter exists; the `runtime_factory`
parameter was removed (M1: dead parameter removal).

This is consistent with the lessons.md guidance: **use `detect_anomalies`
orchestrator, NOT the public `detect_*` wrappers**, to get the baseline-ratio
defense and avoid the known divergence.

---

## 6. Fixture Seeding

### Initial Fixtures (3 required, 1 optional)

| Fixture | Incidents | `must_detect` | Purpose |
|---------|-----------|---------------|---------|
| `sms-abuse-baseline` | 1 × `sms_abuse` (volume ≥ 500) | `true` | Happy path for SMS detector |
| `auth-burst-baseline` | 1 × `auth_failure_burst` (failures ≥ 100, users ≥ 20) | `true` | Happy path for auth detector |
| `noise-only` | none | n/a | FP regression guard — 0 expected, 0 anomalies |
| `mixed-multi-incident` (optional) | SMS + auth in same window | both `true` | Multi-incident aggregation |

### Generation Script

`scripts/build_eval_fixtures.py` — small CLI:

```bash
python scripts/build_eval_fixtures.py [--output-dir tests/fixtures/evaluation]
```

For each fixture:

1. Build `SyntheticLogGenerator(seed=<fixed>)`.
2. Construct `Incident` objects sized to clear `DetectionConfig` defaults.
3. Call `generator.generate(start=..., hours=1, incidents=...)`.
4. Write events via `write_jsonl(events, path)`.
5. Write expected JSON manually (small, hand-curated per fixture).

Script is **idempotent**: running it twice yields byte-identical files.

### Generated Files Are Committed

Fixture files are committed to `tests/fixtures/evaluation/`. The script exists
for regeneration but is not run during tests. Tests load from committed files
to ensure stability across machines.

---

## 7. CLI `eval` Subcommand

Added to `src/opsmitra/cli.py`:

```python
def _cmd_eval(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dataset)
    cases = load_dataset(dataset_dir)
    report = evaluate_all(cases)

    output = format_report_json(report) if args.report == "json" else format_report_table(report)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output)

    # Exit code (H3: strict applies only to must_detect misses)
    critical_missed = sum(len(c.missed_critical) for c in report.cases)
    any_missed = report.totals.get("missed", 0) > 0

    if args.strict:
        return 1 if critical_missed > 0 else 0
    return 2 if any_missed else 0
```

No cooldown injection needed; `evaluate_case` calls `detect_anomalies` directly
and does not touch cooldown state.

---

## 8. Tests (≥15)

### `tests/test_evaluation.py` (12 tests)

1. `test_load_case_parses_events_and_expected` — round-trip from a sample fixture pair (committed).
2. `test_load_case_rejects_missing_expected` — `.expected.json` absent → `FileNotFoundError` with clear message.
3. `test_load_case_rejects_malformed_expected` — invalid JSON → `ValueError` with field-named message.
4. `test_load_dataset_pairs_files_by_stem` — directory with 3 fixture pairs → 3 cases.
5. `test_load_dataset_skips_unpaired_files` — orphan `.events.jsonl` without `.expected.json` → warning + skip.
6. `test_evaluate_case_zero_anomalies_zero_expected` — `noise-only` fixture → detected=0, missed=0, fp=0.
7. `test_evaluate_case_detects_sms_abuse` — `sms-abuse-baseline` → detected=1, missed=0, fp=0.
8. `test_evaluate_case_missed_critical_recorded` — synthetic case with expected incident below detector threshold → `missed_critical` is non-empty.
9. `test_evaluate_case_false_positive_recorded` — case with no expected incidents but generator-injected anomaly → fp ≥ 1.
10. `test_detection_delay_calculated_when_detected` — first_seen vs window_end → delay equals `(window_end - first_seen).total_seconds()`.
11. `test_match_key_normalization` — case/whitespace differences in tenant or subject still match.
12. `test_evaluate_all_aggregates_totals` — 3 cases → totals = sum of per-case counts.
13. `test_report_table_format_contains_columns` — table includes headers `case`, `detected`, `missed`, `fp`, `p50_delay`, `p95_delay`.
14. `test_report_json_format_round_trips` — `json.loads(format_report_json(r))` is structurally equivalent to a manual dict build.

### `tests/test_cli_eval.py` (4 tests)

15. `test_cli_eval_default_dataset_exits_zero` — all 3 committed fixtures detect → exit 0.
16. `test_cli_eval_strict_missed_critical_exits_one` — temp dataset with one impossible-to-detect critical → `--strict` → exit 1.
17. `test_cli_eval_report_json_output_to_stdout` — `--report json` emits valid JSON to stdout.
18. `test_cli_eval_output_file_writes_report` — `--output PATH` → file exists with report content.

### Fixtures Committed

```
tests/fixtures/evaluation/
├── sms-abuse-baseline.events.jsonl       (~808 KB)
├── sms-abuse-baseline.expected.json
├── auth-burst-baseline.events.jsonl      (~773 KB)
├── auth-burst-baseline.expected.json
├── noise-only.events.jsonl               (~593 KB)
└── noise-only.expected.json
```

Each `.events.jsonl` ≤ 1 MB. Hard cap enforced in `load_case` via `_MAX_EVENTS_FILE_BYTES = 1_000_000` to keep CI fast.

---

## 9. Exit Codes (Reiterated)

| Scenario | Exit |
|----------|------|
| All expected incidents detected, no FPs | 0 |
| Some FPs but no misses | 0 |
| Strict mode + no `must_detect=true` misses (non-critical misses OK) | 0 |
| Non-strict + any miss | 2 |
| Strict + missed `must_detect=true` (critical) incident | 1 |
| Runtime/IO error (corrupt fixture, etc.) | 1 |

---

## 10. Risks & Open Questions (Auto-Defaulted)

| Risk | Default Decision |
|------|------------------|
| Detector path — wrappers vs orchestrator | Use `detect_anomalies` orchestrator (per lessons.md L34: known divergence with wrappers). |
| Determinism across machines | Fixture `.events.jsonl` committed to git; not regenerated at test time. Builder script is dev-only. |
| Time source / cooldown bleed | No cooldown used in eval; `evaluate_case` calls `detect_anomalies` directly. |
| Fixture size | Cap each `.events.jsonl` at 1 MB (`_MAX_EVENTS_FILE_BYTES`); `load_case` raises `ValueError` if exceeded. |
| Subject matching strictness | Subset match (expected ⊆ detected) so detector-added keys (e.g. `provider`) don't break matches. |
| Wildcard tenant | `"*"` literal on expected side means "any tenant" — handles auth_burst case where tenant is derived via `_dominant_value`. |
| Atomicity of metrics | Each ExpectedIncident matched at most once, each Anomaly matched at most once — greedy in expected order. |
| Eval harness uses Runtime or detect_anomalies directly? | **Direct call** to `detect_anomalies(events, window_start, window_end)` after loading events. Runtime is bypassed because we need the actual `Anomaly` list, not just counts. This keeps the harness simple and faithful to detector behavior. |

### Open Questions

None requiring user input — all decisions defaulted above. The matching rule
(subset on `subject`) and the runtime-bypass decision are the two items most
worth flagging in the post-implementation review.

---

## 11. Out of Scope

- Live Athena replay against committed fixtures (Step 10's "AWS-window replay" milestone).
- Production metrics emission to CloudWatch / Prometheus.
- HTML report output.
- ROC curves / threshold sweep / sensitivity analysis.
- LLM summarizer evaluation (summary quality scoring).
- Slack alerter dry-run capture in eval.

---

## 12. Phased Implementation Order

| Phase | Deliverable | Files |
|-------|-------------|-------|
| A | `ExpectedIncident`, `EvaluationCase`, `load_case`, `load_dataset` + tests | `src/opsmitra/evaluation.py`, `tests/test_evaluation.py` (load tests 1–5) |
| B | Commit 3 fixture pairs + `scripts/build_eval_fixtures.py` | `tests/fixtures/evaluation/*`, `scripts/build_eval_fixtures.py` |
| C | `evaluate_case` + matching + delay calculation + tests 6–11 | `src/opsmitra/evaluation.py`, `tests/test_evaluation.py` |
| D | `evaluate_all` + report formatters + tests 12–14 | `src/opsmitra/evaluation.py`, `tests/test_evaluation.py` |
| E | CLI `eval` subcommand + tests 15–18 | `src/opsmitra/cli.py`, `tests/test_cli_eval.py` |
| F | `everything-claude-code:verify` then `everything-claude-code:python-review` | n/a |

Each phase is independently reviewable. Phase B must precede Phase E (CLI test
depends on committed fixtures).

---

## 13. Ready-for-TDD Checklist

- [ ] Public dataclasses defined and frozen.
- [ ] Match key normalization rule explicit and testable.
- [ ] Exit code matrix is unambiguous.
- [ ] Fixture format documented with one full example.
- [ ] 3 initial fixtures named with `must_detect` flags.
- [ ] Cooldown isolation strategy stated (per-case `tmp_path`).
- [ ] Detector path decision explicit (orchestrator, not wrappers).
- [ ] Out-of-scope list captures Athena replay, HTML, ROC curves.
- [ ] No new dependencies introduced (uses only stdlib + existing OpsMitra modules).
- [ ] All 18 tests named and mapped to subgoals.

---

## WAITING FOR CONFIRMATION

Proceed to Step 9 Phase A (TDD)? (yes / modify / different approach)
