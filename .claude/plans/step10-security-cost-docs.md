# Step 10 — Security, Cost, and Documentation Hardening (Final Phase)

> **Environment rule (from `.claude/tasks/lessons.md`):** Always invoke `.venv/bin/pytest` and `.venv/bin/python`. NEVER call bare `pytest` or `python`. Subagents must paste the venv path explicitly.

> **Final phase of the OpsMitra build plan.** This phase closes out all carry-over deferrals from Steps 7–9, hardens the security/cost story, documents the system end-to-end with Mermaid diagrams, and codifies the primary learning-goal milestone (AWS-window replay exercise).

---

## 1. Requirements Restatement

### 1.1 Acceptance Criteria (verbatim from build plan)

| # | AC | Evidence | Workstream |
|---|----|----------|------------|
| AC1 | Security review checklist is documented. | `docs/security-checklist.md` exists with N sections; tested. | WS-A |
| AC2 | README can guide a new developer through local demo. | `README.md` has 6 sections including Quickstart, env vars table, demo walkthrough, overview Mermaid; tested. | WS-B |
| AC3 | AWS cost controls and partitioning assumptions are documented. | `docs/aws-cost-controls.md` exists with N sections; tested. | WS-C |
| AC4 | **Primary learning milestone:** documented AWS-window replay exercise. | `docs/aws-window-replay.md` (checklist) + `docs/aws-window-replay-findings.md` (template); tested. | WS-E |

### 1.2 Additional First-Class Deliverable — Mermaid Diagrams

Per explicit user request:

| ID | File | Type | Subject |
|----|------|------|---------|
| D1 | `docs/architecture.md` | `flowchart` | Local + AWS pipeline (one combined or two separate). |
| D2 | `docs/architecture.md` | `sequenceDiagram` | `Runtime.execute` stages with cooldown gate. |
| D3 | `docs/architecture.md` | `flowchart` | Evaluation harness data flow (fixtures → `load_case` → `evaluate_case` → match → `EvaluationReport`). |
| D4 | `README.md` | `flowchart` | 10-second high-level overview (input → detectors → optional summarizer → Slack/dry-run). |

Render target: GitHub-flavored Markdown (verified) + VS Code preview. Fenced as ` ```mermaid ` blocks.

### 1.3 Carry-Over Deferral Inventory (must close in Step 10)

Mined from `.claude/plans/step7-review-fixes.md`, `step8-scheduler-runtime.md` carry-overs, `step9-review-fixes.md`, and `.claude/tasks/lessons.md`.

| Tag | Source | Severity | Title |
|-----|--------|----------|-------|
| **M6** | Step 7 review | HIGH (correctness debt) | Unify `DetectorThresholds` ↔ `DetectionConfig` — wrappers bypass baseline-ratio defense. |
| **M5** | Step 7 review | MEDIUM | Wire `auth_burst_window_seconds` and `cost_runaway_window_seconds` into detectors (loaded, unused). |
| **L3 (S7)** | Step 7 review | LOW | Extract endpoint enum to `models.py` (shared constant, dedupe from `query_builder.py`). |
| **N2** | Step 7 review | NIT | Add timestamp lex-order comment cross-referencing the writer format. |
| **N3** | Step 7 review | NIT | Subprocess env explicitness in AWS isolation test. |
| **M2** | Step 8 review | MEDIUM | Exit-code disambiguation: argparse misuse (currently 2) → 64 (`EX_USAGE`), keep anomalies-detected at 2. |
| **L3 (S8)** | Step 8 review | MEDIUM | Cooldown concurrent-writer race — use `fcntl` advisory lock (zero new deps; DynamoDB documented as future). |
| **N4** | Step 8 review | NIT | Cooldown persist: `fh.flush()` + `os.fsync(fh.fileno())` before `os.replace`. |
| **M4** | Step 9 review | LOW | Streaming event-loading in `evaluate_case` — **DEFER (YAGNI)**; fixtures stay <1 MB. Add TODO referencing cap. |

---

## 2. Workstreams Overview

Five workstreams (broader than prior steps; group by theme):

| WS | Theme | AC mapping | Output |
|----|-------|------------|--------|
| WS-A | Security checklist + secret-handling sweep | AC1 | `docs/security-checklist.md` |
| WS-B | README + architecture docs + Mermaid (D1–D4) | AC2 | `README.md`, `docs/architecture.md` |
| WS-C | AWS cost & partitioning docs | AC3 | `docs/aws-cost-controls.md` |
| WS-D | Carry-over code fixes (correctness debt) | — (enables AC1/AC4 confidence) | Code + tests in `src/opsmitra/`, `tests/` |
| WS-E | AWS-window replay exercise (primary milestone) | AC4 | `docs/aws-window-replay.md`, `docs/aws-window-replay-findings.md` |

---

## 3. WS-A — Security Checklist + Secret-Handling Sweep

### 3.1 New File: `docs/security-checklist.md`

Sections (each is a Markdown H2):

1. **IAM Boundaries** — minimum permissions table; sample IAM policy JSON for Athena read + S3 write.
2. **Secret Handling** — env-var/AWS Secrets Manager guidance; reference Step 6 `_redact` pattern + Step 7 `AthenaQueryError` redaction precedent. No webhook URL in logs (lessons.md line 85).
3. **Model Data Exposure** — summarizer prompt boundary: scoped evidence ≤480 chars; never raw event payloads or webhook URLs (lessons.md privacy boundary).
4. **Athena Cost Risks** — partition predicates REQUIRED; `_MAX_GZIP_BYTES` per-write cap; query-result pagination guidance. Cross-link to `docs/aws-cost-controls.md`.
5. **Operational Failure Modes** — table covering: cooldown corruption (silent reset + WARN), Slack outages (retry policy + fail-fast on 4xx/429), Athena failures (`AthenaQueryError`), model timeouts (`FallbackSummarizer` fallback). Each row links to the file + lessons.md ref.
6. **Pre-Commit Security Checklist** — copied/adapted from `common/security.md` rules and tailored to OpsMitra-specific items (no `OPSMITRA_SLACK_WEBHOOK_URL` in fixtures, no real AWS account IDs in tests, etc.).

### 3.2 Sample IAM Policy (in section 1)

JSON inline:
- `athena:StartQueryExecution`, `athena:GetQueryExecution`, `athena:GetQueryResults` on the workgroup ARN.
- `s3:GetObject`, `s3:ListBucket` on the Athena output-location bucket.
- `s3:PutObject` on the events-bucket prefix.
- `glue:GetTable`, `glue:GetPartitions` on the `opsmitra` database.
- Nothing else.

### 3.3 Test

`tests/test_docs.py::test_security_checklist_sections_exist` — assert all 6 H2 headings present (`re.search(r"^## ", ...)`).

---

## 4. WS-B — README + Architecture + Mermaid

### 4.1 `README.md` Rewrite

Required sections (H2 headings), in order:

1. **Overview** — current intro + D4 overview Mermaid `flowchart` block.
2. **Quickstart** — minimum commands to run eval + run, using `.venv/bin/python -m opsmitra ...`:
   ```bash
   .venv/bin/pip install -e .[dev]
   .venv/bin/python -m opsmitra eval --dataset tests/fixtures/evaluation
   .venv/bin/python -m opsmitra run --window-start 2026-06-01T00:00:00Z --window-end 2026-06-01T01:00:00Z --dry-run
   ```
3. **Environment Variables** — Markdown table of all ~20 `OPSMITRA_*` vars from `config.py`. Columns: `Name | Default | Purpose`. Cross-link to `docs/security-checklist.md` for secret vars.
4. **Local Demo Walkthrough** — 5-minute step-by-step: clone → venv → install → generate synthetic events → run detectors → see dry-run Slack payload.
5. **Architecture** — short prose + link to `docs/architecture.md`.
6. **AWS Deployment Notes** — short prose + links to `docs/aws-cost-controls.md` + `docs/security-checklist.md` + `docs/aws-window-replay.md`.

### 4.2 D4 — Overview Mermaid (in README §Overview)

```
flowchart LR
    A[Input<br/>synthetic or S3] --> B[Detectors]
    B -->|anomalies| C{Anomalies?}
    C -->|none| D[Exit 0]
    C -->|yes| E[Summarizer<br/>optional]
    E --> F[Slack alert<br/>or dry-run]
    F --> G[Exit 2 if alerted]
```

(Final block authored in implementation phase; structure above is the prescriptive sketch.)

### 4.3 `docs/architecture.md` Extension

Keep existing prose. Add three Mermaid blocks at well-labeled positions:

- **D1 — Pipeline `flowchart`** (after §Runtime Modes): combined local+AWS with a mode-branch on the input source. Nodes: `Generator`, `LocalNDJSONEventSource` / `AthenaEventSource`, `detect_anomalies`, `Summarizer` (FallbackSummarizer fallback), `SlackAlerter` (dry-run gate), `S3NDJSONEventSink` (AWS sink).
- **D2 — `sequenceDiagram` for `Runtime.execute`** (in §Components → new sub-section "Runtime Sequence"): actors `Runtime`, `EventSource`, `Detectors`, `Cooldown`, `Summarizer`, `SlackAlerter`. Stages: validate → fetch → detect → for-each anomaly: cooldown.should_alert? → (skip OR summarize → alert → record) → persist.
- **D3 — Evaluation harness `flowchart`** (new §Evaluation): `Fixtures (events.jsonl + expected.json)` → `load_case` → `evaluate_case` (which internally fans out: `detect_anomalies` + `match`) → `CaseResult` → `EvaluationReport`.

### 4.4 Tests

`tests/test_docs.py`:
- `test_readme_sections_exist` — assert all 6 H2 headings present.
- `test_readme_contains_overview_mermaid` — grep for ` ```mermaid` count ≥1.
- `test_architecture_contains_three_mermaid_blocks` — grep ` ```mermaid` count ≥3.

---

## 5. WS-C — AWS Cost & Partitioning Docs

### 5.1 New File: `docs/aws-cost-controls.md`

Sections (H2):

1. **Partition Strategy** — `tenant/year/month/day/hour` 5-key Hive layout (lessons.md line 31). Why partition predicates are mandatory. Concrete example of a query with and without predicates and the resulting scanned-bytes delta.
2. **Athena Scan-Byte Estimation** — quick rule-of-thumb: bytes-scanned ≈ Σ(partition sizes matching predicate). How to inspect via `GetQueryExecution.Statistics.DataScannedInBytes`. Per-query cost example at $5/TB.
3. **S3 Storage Classes** — Intelligent-Tiering for hot partitions; Glacier Instant Retrieval for >30 day cold.
4. **Workgroup Per-Query Byte Cap** — recommend `BytesScannedCutoffPerQuery` at workgroup level as defense-in-depth.
5. **In-Process Cost Controls** — env-var table:
   - `OPSMITRA_MAX_EVENTS_PER_WINDOW` (today) — caps events fetched per window.
   - `OPSMITRA_ATHENA_MAX_BYTES_SCANNED` (future) — caps per-query scan.
6. **Dogfooding the Cost-Runaway Detector** — note that the cost-runaway detector itself can monitor Athena query cost via emitted CloudWatch metrics.

### 5.2 Test

`tests/test_docs.py::test_aws_cost_doc_sections_exist` — assert 6 H2 headings present.

---

## 6. WS-D — Carry-Over Code Fixes (Correctness Debt)

> **Phase A is the highest-risk change in Step 10.** Land it first while the codebase is otherwise stable.

### 6.1 M6 (HIGHEST PRIORITY) — Unify `DetectorThresholds` ↔ `DetectionConfig`

**Problem (lessons.md line 42 + line 87):** Public `detect_*` wrappers use `DetectorThresholds` directly; they intentionally bypass the baseline-ratio defense that the orchestrator (`detect_anomalies`) applies via `DetectionConfig`. The KNOWN DIVERGENCE is documented but unsafe — Runtime correctly uses the orchestrator, but any external caller using the wrappers gets weaker detection.

**Chosen approach (Option A — extend orchestrator):**

1. `detect_anomalies(events, *, thresholds: DetectorThresholds | None = None, config: DetectionConfig | None = None)` — new `thresholds` parameter.
2. When `thresholds is not None`: derive an effective `DetectionConfig` by overriding the corresponding fields (mapping table below). The `baseline_ratio` and `confidence_floor` from `DetectorThresholds` override the orchestrator defaults; per-detector knobs (`sms_abuse_*`, `auth_burst_*`, `endpoint_error_*`, `cost_runaway_*`) override the corresponding `DetectionConfig` fields.
3. Field-mapping table (documented inline in `detectors.py`):
   | `DetectorThresholds` field | `DetectionConfig` field |
   |---|---|
   | `sms_abuse_rate_per_minute` | `sms_abuse_rate_per_minute` |
   | `auth_burst_count` | `auth_burst_count` |
   | `auth_burst_window_seconds` | `auth_burst_window_seconds` (newly wired — see M5) |
   | `endpoint_error_rate` | `endpoint_error_rate` |
   | `endpoint_error_min_samples` | `endpoint_error_min_samples` |
   | `cost_runaway_delta_usd` | `cost_runaway_delta_usd` |
   | `cost_runaway_window_seconds` | `cost_runaway_window_seconds` (newly wired — see M5) |
4. `Runtime._compose_and_execute` (in `runtime.py`): resolve thresholds via `resolve_thresholds(...)` then pass through to `detect_anomalies(..., thresholds=resolved)`.
5. Remove the lessons.md KNOWN DIVERGENCE warning (now resolved).

**Tests** (`tests/test_runtime_thresholds.py`, new):
- `test_runtime_passes_thresholds_to_orchestrator` — fake event source + spy orchestrator; assert thresholds arg shape.
- `test_threshold_overrides_change_detection_outcome` — same events, different thresholds → different anomaly count.
- `test_thresholds_compose_with_baseline_ratio_defense` — confirm baseline-ratio defense still applies (no regression on the orchestrator path).

### 6.2 M5 — Wire window seconds

`auth_burst_window_seconds` and `cost_runaway_window_seconds` are loaded into `DetectorThresholds` but unused by the detectors (TODO at config.py:46). Wire them through:

1. Add corresponding fields to `DetectionConfig` if missing.
2. `detect_auth_failure_burst` and `detect_cost_runaway` consume the window length from `DetectionConfig`.
3. Tests in `tests/test_detectors.py` extension (2 tests): tighter window → fewer anomalies; wider window → more anomalies.

### 6.3 L3 (Step 7) — Endpoint enum to `models.py`

Move the `EVENT_TYPES = frozenset({"sms_send", "auth_login", "api_request", "cost_meter"})` (whatever the current set is in `query_builder.py`) to `src/opsmitra/models.py` as `EVENT_TYPES`. Import from `models` in `query_builder.py`.

Test (`tests/test_query_builder.py` addition): `from opsmitra.models import EVENT_TYPES` + assert query_builder uses that import (introspect via `query_builder.EVENT_TYPES is EVENT_TYPES`).

### 6.4 L3 (Step 8) — Cooldown `fcntl` lock

**Chosen approach:** `fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)` on POSIX. Skip on `sys.platform == "win32"` with a single-line `logger.debug` (cooldown is single-process v0 anyway). DynamoDB documented as future work in `docs/security-checklist.md` §Operational Failure Modes.

Changes to `cooldown.py`:
1. In `persist()`: open temp file, acquire exclusive lock NON-BLOCKING, on `BlockingIOError` log `WARNING("cooldown persist skipped: another writer holds the lock")` and return without writing. Cooldown is perf, not correctness — skipping is safe.
2. Add a `_lock_supported` module-level check based on platform.

Test (`tests/test_cooldown.py` addition): use threading (NOT subprocess — keeps test fast). Acquire lock in thread 1, attempt `persist()` in main thread, assert WARNING logged and main thread did not write. Skip test on Windows via `pytest.mark.skipif(sys.platform == "win32", ...)`.

### 6.5 N4 — fsync before replace

In `cooldown.persist()`: after writing to temp file, before `os.replace`:
```python
fh.flush()
os.fsync(fh.fileno())
```

Test (`tests/test_cooldown.py` addition): mock `os.fsync` and assert it was called with the temp-file fd before `os.replace`.

### 6.6 M2 — Exit-code disambiguation

**Problem:** Today `argparse` misuse and "anomalies detected" both exit 2 (lessons.md line 50). Disambiguate by subclassing `ArgumentParser`:

```python
class _OpsMitraArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        sys.exit(64)  # EX_USAGE (sysexits.h)
```

Update `build_parser()` in `cli.py` to instantiate `_OpsMitraArgumentParser`. Keep main exit codes: 0 / 1 (runtime) / 2 (anomalies) / 64 (usage).

Test (`tests/test_cli.py` addition): invoke parser with bad args, assert `SystemExit.code == 64`.

Doc update: update `docs/architecture.md` exit-code section (if any) + the CLI `--help` epilog string + lessons.md entry.

### 6.7 N2 — Timestamp lex-order comment

In `event_source.py` (local source) and in the Athena `query_builder.py` partition logic: add cross-referencing comments noting timestamps are lex-sortable in ISO-8601 with Z suffix, which is why partition iteration order matches event order. No code change.

### 6.8 N3 — Subprocess env explicitness

In `tests/test_event_source.py::test_event_source_does_not_import_boto3` (or wherever the subprocess isolation test lives): change `subprocess.run([...])` to `subprocess.run([...], env=os.environ.copy())` to make environment inheritance explicit. No behavioral change.

### 6.9 M4 — Defer (final)

Add a single-line `# TODO(post-step-10): streaming evaluator if fixtures > 1 MB cap` in `evaluator.py` near the `list(...)` call. No code change beyond the comment.

---

## 7. WS-E — AWS-Window Replay Exercise (Primary Milestone)

### 7.1 New File: `docs/aws-window-replay.md`

Documentation-only (the agent does not run the exercise — the user does). REPEATABLE checklist.

Sections (H2):

1. **Purpose** — restate this is the primary learning milestone of the OpsMitra project (build plan line 103).
2. **Prerequisites** — AWS account, S3 bucket, Athena workgroup, IAM role with the policy from `docs/security-checklist.md` §IAM Boundaries.
3. **Pick a Data Source** — pick one (one-time decision):
   - OpsMitra's own generated events (already in S3 from Step 7).
   - CloudTrail events.
   - S3 access logs.
   - Generic API access logs.
4. **Configure Environment** — example env-var block:
   ```bash
   export OPSMITRA_EVENT_SOURCE=athena
   export OPSMITRA_AWS_REGION=us-east-1
   export OPSMITRA_S3_BUCKET=...
   export OPSMITRA_ATHENA_DATABASE=opsmitra
   export OPSMITRA_ATHENA_OUTPUT_LOCATION=s3://...
   export OPSMITRA_DRY_RUN=true
   ```
5. **Run the Replay** — command:
   ```bash
   .venv/bin/python -m opsmitra run \
     --window-start <ISO8601> \
     --window-end <ISO8601> \
     --source athena \
     --dry-run
   ```
6. **Capture Findings** — instruction to fill in `docs/aws-window-replay-findings.md`.
7. **Iterate** — guidance on adjusting thresholds via `OPSMITRA_THRESHOLDS_PATH` if findings are noisy or sparse.

### 7.2 New File: `docs/aws-window-replay-findings.md`

A frozen TEMPLATE the user fills in post-exercise. Sections (H2):

1. **Window Replayed** — ISO timestamps, source type.
2. **Anomalies Detected** — table: `Type | Tenant | Subject | Severity | Expected?`.
3. **False Positives** — list of anomalies fired that were not real incidents; root cause.
4. **Missed Incidents** — list of real incidents the detectors did NOT catch; root cause.
5. **Athena Cost** — bytes scanned, $ cost.
6. **Detection Delay (Upper Bound)** — observed `detection_delay_upper_bound_seconds` per anomaly.
7. **Threshold Adjustments Applied** — JSON snippet of any overrides used.
8. **Next Steps** — list of detector improvements or new detectors suggested by the exercise.

### 7.3 Tests

`tests/test_docs.py`:
- `test_aws_window_replay_exercise_doc_exists` — assert file present + 7 H2 sections.
- `test_aws_window_replay_findings_template_exists` — assert file present + 8 H2 sections.

---

## 8. Test Plan Summary

New tests across WS-A through WS-E (approximate count: 15):

| File | Test | WS |
|------|------|----|
| `tests/test_docs.py` | `test_security_checklist_sections_exist` | A |
| `tests/test_docs.py` | `test_readme_sections_exist` | B |
| `tests/test_docs.py` | `test_readme_contains_overview_mermaid` | B |
| `tests/test_docs.py` | `test_architecture_contains_three_mermaid_blocks` | B |
| `tests/test_docs.py` | `test_aws_cost_doc_sections_exist` | C |
| `tests/test_docs.py` | `test_aws_window_replay_exercise_doc_exists` | E |
| `tests/test_docs.py` | `test_aws_window_replay_findings_template_exists` | E |
| `tests/test_runtime_thresholds.py` | `test_runtime_passes_thresholds_to_orchestrator` | D (M6) |
| `tests/test_runtime_thresholds.py` | `test_threshold_overrides_change_detection_outcome` | D (M6) |
| `tests/test_runtime_thresholds.py` | `test_thresholds_compose_with_baseline_ratio_defense` | D (M6) |
| `tests/test_detectors.py` | `test_auth_burst_window_seconds_honored` | D (M5) |
| `tests/test_detectors.py` | `test_cost_runaway_window_seconds_honored` | D (M5) |
| `tests/test_query_builder.py` | `test_endpoint_enum_sourced_from_models` | D (L3-S7) |
| `tests/test_cooldown.py` | `test_persist_skips_when_lock_held` | D (L3-S8) |
| `tests/test_cooldown.py` | `test_persist_fsyncs_before_replace` | D (N4) |
| `tests/test_cli.py` | `test_argparse_misuse_exits_with_64` | D (M2) |

All tests must pass without external AWS; doc tests are pure file-content assertions.

---

## 9. Phased Order

| Phase | Work | Rationale |
|-------|------|-----------|
| **A** | WS-D code: **M6 first, then M5** | Biggest correctness debt; land while codebase is otherwise quiet. |
| **B** | WS-D minor code: L3×2, N2, N3, N4, M2 | Low-risk; clears the carry-over list. |
| **C** | WS-A security checklist | Single doc; informs the README's security section. |
| **D** | WS-C AWS cost doc | Single doc; informs the README's AWS section. |
| **E** | WS-B README + architecture.md + Mermaid (D1–D4) | Composes WS-A + WS-C output into the user-facing entry point. |
| **F** | WS-E AWS-window replay docs + template | Final milestone; depends on stable security + cost docs. |
| **G** | Verify + Review | Full test suite + python-review skill. |

---

## 10. Risks & Open Questions (Auto-Defaulted)

| Risk | Default Resolution |
|------|--------------------|
| M6 unification: extend orchestrator vs adapter? | **Option A — extend orchestrator** (simpler; one code path; no parallel surface). |
| Cooldown lock: `fcntl` vs `portalocker`? | **`fcntl`** — zero new deps, Unix-only, document Windows skip. |
| Mermaid rendering target? | **GitHub-flavored Markdown** (renders natively). VS Code preview as secondary. |
| AWS-window replay execution scope? | **Documentation-only this step.** User runs the actual exercise; we ship the checklist + findings template. |
| README env-var table maintenance burden? | **Acceptable** — config surface is stable; revisit only if config.py adds ≥5 new vars. |

---

## 11. Out of Scope (Final Deferrals — Post-Step-10)

- **Streaming evaluator** (Step 9 M4) — fixtures stay <1 MB; YAGNI.
- **DynamoDB cooldown backend** — `fcntl` covers single-host v0; multi-host alerting is a future architecture.
- **Glue Crawler / Catalog automation** — Athena schema management out of scope; manual DDL for now.
- **Parquet conversion of S3 sink** — JSONL.gz is sufficient at v0 scale; Parquet is a Step-11+ optimization.
- **Production metrics emission** — CloudWatch metrics export deferred; the cost-runaway-on-itself note in WS-C is documentation-only.
- **GitHub Actions CI** — separate workstream; not part of the build plan.

---

## 12. Ready-for-TDD Checklist

- [x] All ACs map to a workstream.
- [x] All carry-over deferrals from Steps 7–9 are either closed in WS-D or explicitly final-deferred in §11.
- [x] All 4 Mermaid diagrams (D1–D4) are scoped with rendering target.
- [x] Highest-risk change (M6) has a chosen approach (Option A) with explicit field-mapping table.
- [x] Test plan enumerates ~15 new tests with locations.
- [x] Phased order isolates highest-risk change (M6) to Phase A.
- [x] AWS-window replay scope is clarified (docs-only; user executes).
- [x] No new external dependencies introduced (no `portalocker`, no `boto3` in event_source).

---

**WAITING FOR CONFIRMATION**: Proceed with this plan? (yes / modify / different approach)
