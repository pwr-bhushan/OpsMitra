# Step 7: Fix Plan — Address Step 7 Review Findings

**Step 7 Plan reference**: [`.claude/plans/step7-aws-s3-athena.md`](./step7-aws-s3-athena.md)
**Review verdict from Step 7**: **APPROVE WITH MINORS** (no CRITICAL)
**Branch**: `dev` (mandatory per `.claude/tasks/lessons.md`)
**Workflow phase**: FIX PLAN (no code in this phase)
**Findings inventory source**: [`.claude/sessions/2026-06-07-opsmitra-step7-paused.md`](../sessions/2026-06-07-opsmitra-step7-paused.md) — section "REVIEW Findings (for resume)"

---

## 1. Scope of This Fix Pass

The Step 7 `python-review` returned **APPROVE WITH MINORS**. Of the 22 findings
raised, the main session has selected **17** for this fix pass and **explicitly
deferred 5** to Step 10 (Hardening). This plan applies:

- **5 HIGH** — H1, H2, H3, H4, H5 (all applied — no CRITICAL exists)
- **5 MEDIUM** — M1, M2, M3, M4, M7
- **4 LOW** — L1, L2, L4, L5
- **3 NIT** — N1, N4, N5

**Deferred to Step 10** (see §6 table): M5, M6, L3, N2, N3.

### 1.1 Findings addressed in this fix pass

| #  | Sev   | File:Line                                | One-line description                                                                                       |
|----|-------|------------------------------------------|------------------------------------------------------------------------------------------------------------|
| H1 | HIGH  | `aws/athena_source.py:107`               | Header parsing uses `.get("VarCharValue", "")` to tolerate NULL header cells.                              |
| H2 | HIGH  | `aws/athena_source.py:61`                | Tighten `types: list[str] \| None` → `Sequence[str] \| None` to match `EventSource` Protocol.              |
| H3 | HIGH  | `aws/athena_source.py:115-126`           | Dedicated `AthenaQueryError("polling exceeded…")` distinct from real FAILED state; injectable poll cadence. |
| H4 | HIGH  | `aws/s3_sink.py:77-83`                   | Embed `batch_id = uuid.uuid4().hex[:12]` in object key → `events-{batch_id}.jsonl.gz`; no silent overwrite. |
| H5 | HIGH  | `aws/s3_sink.py:105-112`                 | Cap in-memory gzip at `_MAX_GZIP_BYTES = 100_000_000`; raise `ValueError` over cap; WARNING at half.        |
| M1 | MED   | `config.py:108`                          | Bound thresholds JSON file at `_MAX_THRESHOLDS_FILE_BYTES = 1_000_000` (1 MB).                              |
| M2 | MED   | `config.py:103`                          | Translate `FileNotFoundError` → `ValueError("thresholds file not found: %s" % path)`.                       |
| M3 | MED   | `aws/athena_source.py:98`                | `logger.warning("athena query %s ended in state %s", execution_id, state)` before raising.                  |
| M4 | MED   | `aws/query_builder.py:50`                | Use precompiled `_TENANT_RE` instead of inline `re.fullmatch`.                                              |
| M7 | MED   | `event_source.py:91`                     | Replace silent `except: continue` with `logger.debug("skipping malformed NDJSON line %d: %s", lineno, exc)`. |
| L1 | LOW   | `aws/athena_source.py:15`                | Drop unused `from opsmitra.event_source import EventSource  # noqa`.                                       |
| L2 | LOW   | `aws/athena_source.py:51-53`             | Comment explaining `_time_module = time` alias semantics under `patch("time.sleep", ...)`.                  |
| L4 | LOW   | `detectors.py:218` (×4 wrappers)         | `thresholds: DetectorThresholds \| None = None` then `thresholds = thresholds or DetectorThresholds()`.    |
| L5 | LOW   | `config.py:226-227`                      | Replace `# type: ignore[arg-type]` with `cast(Literal["local","athena"], event_source_kind)` (and sink).   |
| N1 | NIT   | `event_source.py:38`                     | Add docstrings on `EventSource.fetch_events` and `EventSink.write_events`.                                  |
| N4 | NIT   | `tests/test_athena_source.py:213-216`    | Remove `executed_queries` and `capture_start` dead variables.                                               |
| N5 | NIT   | `aws/athena_source.py:30`                | Comment listing non-terminal states (`QUEUED`, `RUNNING`).                                                  |

### 1.2 Findings explicitly DEFERRED (Step 10 Hardening candidates)

See §6 for the full deferral table with rationale.

---

## 2. Phased Fix Sequence

Edits are grouped by risk and applied in this exact order so each phase is
small, reviewable, and verifiable before the next begins.

### Phase 1 — Cheap polish (no behavior change)

Targets: **N1, N4, N5, L1, L2, L5**.

- **N1** — Add one-line docstrings to `EventSource.fetch_events` and
  `EventSink.write_events` describing semantics (filter contract, idempotency
  expectations on writes).
- **N4** — Delete dead `executed_queries` and `capture_start` locals in
  `tests/test_athena_source.py:213-216`.
- **N5** — Add inline comment in `aws/athena_source.py:30` listing the
  non-terminal Athena states (`QUEUED`, `RUNNING`) the poll loop tolerates.
- **L1** — Remove unused `from opsmitra.event_source import EventSource  # noqa`
  import at `aws/athena_source.py:15`.
- **L2** — Add 1-line comment at `aws/athena_source.py:51-53` explaining the
  `_time_module = time` alias (necessary because tests `patch("time.sleep",…)`
  and the source needs an un-patched handle for assertion-only timing paths).
- **L5** — Replace `# type: ignore[arg-type]` in `config.py:226-227` with
  `cast(Literal["local", "athena"], event_source_kind)` (and the analogous cast
  for the sink kind). Adds `from typing import cast, Literal` if not already
  imported.

**Verifier**: `ruff check`, `black --check`, full suite green — no behavior change expected.

### Phase 2 — Config bounds + observability

Targets: **M1, M2, M3, M5 (TODO marker only), M7**.

- **M1** — At top of `config.py` add `_MAX_THRESHOLDS_FILE_BYTES = 1_000_000`.
  Before reading the thresholds JSON, `stat(path).st_size > _MAX_THRESHOLDS_FILE_BYTES`
  → `raise ValueError(f"thresholds file exceeds {_MAX_THRESHOLDS_FILE_BYTES} bytes: {path}")`.
- **M2** — Wrap the open() in `try: … except FileNotFoundError: raise ValueError(f"thresholds file not found: {path}") from None`.
- **M3** — In `aws/athena_source.py:98`, immediately before raising
  `AthenaQueryError` for non-SUCCEEDED terminal state, add
  `logger.warning("athena query %s ended in state %s", execution_id, state)`.
  Execution ID is an AWS-generated UUID; safe to log.
- **M5 (defer w/ marker)** — At `config.py:42-48` add a single-line
  `# TODO(step-10-hardening): wire window seconds into detector windows` above
  the unused `auth_burst_window_seconds` / `cost_runaway_window_seconds`
  fields. **No behavior change** — this is the deferral marker, not the fix.
- **M7** — In `event_source.py:91`, replace `except Exception: continue` with:
  ```python
  except (json.JSONDecodeError, ValueError) as exc:
      logger.debug("skipping malformed NDJSON line %d: %s", lineno, exc)
      continue
  ```
  Add `lineno` to the `enumerate(file, start=1)` loop.

**Verifier**: new tests for M1 (oversize), M2 (missing file), M7 (malformed-line DEBUG capture); existing tests stay green.

### Phase 3 — Athena hardening

Targets: **H1, H2, H3, M4**.

- **H1** — At `aws/athena_source.py:107`, replace `col["VarCharValue"]` with
  `col.get("VarCharValue", "")`. Matches the existing defensive parsing already
  used for data rows.
- **H2** — Change signature
  `def fetch_events(self, …, types: list[str] | None = None)` →
  `types: Sequence[str] | None = None`. Add `from collections.abc import Sequence`
  if needed. No call-site changes (lists are Sequences).
- **H3** — Introduce two `__init__` parameters with defaults matching today's
  hardcoded values:
  - `poll_interval: float = 2.0`
  - `max_polls: int = 300`
  Store as `self._poll_interval`, `self._max_polls`. In the poll loop, when
  the count reaches `self._max_polls` and the state is still non-terminal
  (`QUEUED`/`RUNNING`), `raise AthenaQueryError(f"polling exceeded {self._max_polls} attempts; query {execution_id} may still be running")`.
  Today's path that emits `state="FAILED"` is reserved exclusively for actual
  Athena-reported FAILED terminal states.
- **M4** — At `aws/query_builder.py:50`, replace inline `re.fullmatch(r"…", tenant)`
  with the module-level precompiled `_TENANT_RE.fullmatch(tenant)`. Constant
  already exists at the top of the file.

**Verifier**: 4 new tests (H1 null header, H3 timeout + custom cadence, M3 WARNING log carries execution_id); existing 15 query_builder + 4 athena_source tests stay green.

### Phase 4 — S3 hardening (most invasive)

Targets: **H4, H5**.

- **H4** — At `aws/s3_sink.py:77-83`:
  - `import uuid` at top.
  - Compute `batch_id = uuid.uuid4().hex[:12]` per `write_events` call.
  - Object key changes from `…/hour=NN/events.jsonl.gz` →
    `…/hour=NN/events-{batch_id}.jsonl.gz`.
  - This eliminates the silent-overwrite hazard when the same partition is
    written twice in one process.
- **H5** — At `aws/s3_sink.py:105-112`:
  - Add module constants `_MAX_GZIP_BYTES = 100_000_000` and
    `_GZIP_WARN_BYTES = _MAX_GZIP_BYTES // 2`.
  - After `gzip.compress(payload_bytes)`, if `len(compressed) > _MAX_GZIP_BYTES`
    → `raise ValueError(f"gzip payload exceeds {_MAX_GZIP_BYTES} bytes ({len(compressed)})")`.
  - If `len(compressed) > _GZIP_WARN_BYTES` (and ≤ cap)
    → `logger.warning("gzip payload %d bytes exceeds half of cap %d", len(compressed), _MAX_GZIP_BYTES)`.

**Verifier**: 2 new tests (H4 key regex shape, H5 oversize ValueError); existing 6 s3_sink tests stay green.

### Phase 5 — Detector signature polish + divergence docstring

Targets: **L4, M6 (docstring only — code unification deferred)**.

- **L4** — For all 4 public wrappers (`detect_request_spikes`, `detect_error_storms`,
  `detect_auth_bursts`, `detect_cost_runaways`, and similar wrappers per the
  current public API in `detectors.py:213-275`):
  ```python
  def detect_request_spikes(
      events: Sequence[Event],
      thresholds: DetectorThresholds | None = None,
  ) -> list[Anomaly]:
      thresholds = thresholds or DetectorThresholds()
      …
  ```
  Mutable-default-by-dataclass-instance is mild; the `| None = None` idiom is
  the project standard (already used elsewhere).
- **M6 (defer code change; apply loud docstring)** — At the module header of
  `detectors.py` (above the 4 wrappers), add a `"""` block:
  > **WARNING**: These thin `detect_*(events, thresholds)` wrappers consume
  > `DetectorThresholds`. The legacy orchestrator `detect_anomalies(events, config)`
  > consumes `DetectionConfig` and applies a ratio defense in `detect_sms_abuse`
  > not present in the wrappers. **Do NOT mix wrapper calls with `detect_anomalies`
  > on the same time window.** Unifying these two surfaces is a Step 10
  > hardening candidate. See `lessons.md`.

  The lesson itself is added to `lessons.md` in COMPLETE (Step 9), **not** in
  this fix pass.

**Verifier**: existing 5 `test_detectors_with_thresholds.py` tests adapt to the new optional signature (call sites with explicit `thresholds=` keep working); no new behavior.

---

## 3. New & Updated Tests — Consolidated List

Approximately **10 new tests** across 4 files. No existing test bodies change
except `test_detectors_with_thresholds.py` if its constructors relied on the
old positional default (likely already keyword-only — verify in FIX phase).

### 3.1 `tests/test_athena_source.py` — +4 tests, N4 cleanup

1. `test_fetch_events_tolerates_null_header_cell` (H1) —
   stub `GetQueryResults` with a header row missing `VarCharValue`; expect
   empty-string column header, no `KeyError`.
2. `test_polling_timeout_raises_athena_query_error_not_failed` (H3) —
   stub responses that always return `RUNNING`; with `max_polls=3`,
   `poll_interval=0`, expect `AthenaQueryError` whose message contains
   `"polling exceeded"` and the execution ID.
3. `test_custom_poll_interval_and_max_polls_honored` (H3) —
   construct `AthenaEventSource(..., poll_interval=0.01, max_polls=5)` and
   assert `time.sleep` call count == 5 (or equivalent observable).
4. `test_pre_raise_warning_log_carries_execution_id` (M3) —
   stub a terminal `FAILED` state; capture logs with `caplog`; assert
   the WARNING record contains the execution_id string.

Plus: delete dead `executed_queries` / `capture_start` locals (**N4**) at
`:213-216` of the existing test file.

### 3.2 `tests/test_s3_sink.py` — +2 tests

5. `test_object_key_embeds_batch_id` (H4) — write a small batch; assert
   the captured `put_object` Key matches the regex
   `r"events-[0-9a-f]{12}\.jsonl\.gz$"`.
6. `test_write_events_rejects_oversize_partition` (H5) — monkeypatch
   `_MAX_GZIP_BYTES` (or pass a large synthetic payload) so the compressed
   output exceeds the cap; assert `ValueError` raised before any
   `put_object` call.

### 3.3 `tests/test_config.py` — +2 tests

7. `test_thresholds_file_too_large_raises_value_error` (M1) — create a
   temp JSON file > 1 MB; expect `ValueError` mentioning `thresholds file
   exceeds`.
8. `test_thresholds_file_missing_raises_value_error` (M2) — set
   `OPSMITRA_THRESHOLDS_PATH=/nonexistent.json`; expect `ValueError`
   mentioning `thresholds file not found`, **not** `FileNotFoundError`.

### 3.4 `tests/test_event_source.py` — +1 test

9. `test_malformed_ndjson_line_logs_debug_with_lineno` (M7) — write a
   2-line NDJSON file with line 2 corrupted; with `caplog` at DEBUG, assert
   one DEBUG record contains `"line 2"` (or the lineno) and the parser
   yields exactly 1 event.

### 3.5 No new tests required for

- **H2** — type-only signature tightening; `mypy --strict` would catch
  divergence but we rely on the existing Protocol conformance test (if any)
  and the static check in CI.
- **M4** — using the precompiled regex is observably identical; the existing
  15 `test_query_builder.py` tests cover the behavior.
- **L1, L2, L4, L5, N1, N5** — refactors / comments / docstrings only.

---

## 4. Acceptance

After the FIX phase runs and RE-VERIFY completes:

1. **All tests pass**: `pytest -q` shows green. Existing **133 tests** + the
   **~10 new tests** enumerated in §3.
2. **Coverage targets**:
   - `src/opsmitra/aws/athena_source.py` ≥ **95%** (was 93%).
   - `src/opsmitra/aws/s3_sink.py` ≥ **95%** (was 100% — small dip allowed
     due to new oversize/half-cap branches that are partly exercised).
   - `src/opsmitra/config.py` ≥ **97%** (sustained).
   - `src/opsmitra/event_source.py` ≥ **92%** (sustained — M7 adds a covered branch).
   - Suite total ≥ **95%** (sustained).
3. **All 4 Step 7 acceptance criteria still PASS** (re-verified verbatim
   per `.claude/plans/step7-aws-s3-athena.md`):
   - AC1 — AWS isolation: `boto3` never imported by `event_source`.
   - AC2 — Partition + injection tests still green (incl. tenant regex).
   - AC3 — All AWS tests use `botocore.stub.Stubber`; zero real-creds refs.
   - AC4 — Threshold precedence endpoint > tenant > default still holds.
4. **No regression on Steps 1–6** — all 74 baseline tests from those steps
   remain green.
5. **`ruff` + `black` clean** on `src/` and `tests/`.

---

## 5. Risks

| Phase | Risk | Mitigation |
|------|------|------------|
| 1 | `cast()` swap (L5) loses runtime check the `# type: ignore` masked. | `cast` is type-system-only; runtime semantics unchanged. The upstream validation that the kind is `"local"` / `"athena"` already lives in `_load_aws_config`. |
| 2 | M2 swallows real OSError (permission denied, etc.). | We catch `FileNotFoundError` **only**; other OSErrors propagate untouched. |
| 2 | M1 cap (1 MB) might reject legitimate large multi-tenant configs. | 1 MB ≈ tens of thousands of threshold entries — order-of-magnitude headroom. Cap is a constant; trivially raised in Step 10 if hit. |
| 3 | H3 timeout error class change breaks any caller that catches the old generic FAILED-state error. | `AthenaQueryError` is the existing class; only the message differs and a new distinguishing prefix is added. No new exception type. |
| 3 | H2 type tightening could fail downstream callers using `list[str]` strictly. | `list[str]` *is* a `Sequence[str]`; Python's structural typing accepts both. mypy variance is covariant on `Sequence` here. |
| 4 | H4 key change is a backward-incompatible naming change. | Step 7 is a new feature; no prior S3 data exists. Documented in plan. |
| 4 | H5 100 MB cap could reject a legitimate hourly partition. | OpsMitra hourly partitions are ≪ 100 MB by design (synthetic generator caps event rate). If a real deployment ever hits this, the WARNING at 50 MB gives 1 doubling of headroom. |
| 5 | L4 mutable-default removal must be applied to **all 4 wrappers** consistently. | FIX-agent instructions enumerate all 4 by name. |
| 5 | M6 docstring is informational only — a careless caller can still mix wrappers + orchestrator. | Acceptable for this pass; full unification scheduled for Step 10. |

---

## 6. Deferred Items (Step 10 Hardening candidates)

| ID  | File:Line                       | Rationale                                                                                                          | Target  |
|-----|---------------------------------|--------------------------------------------------------------------------------------------------------------------|---------|
| M5  | `config.py:42-48`               | `auth_burst_window_seconds` / `cost_runaway_window_seconds` not yet consumed by detectors; wiring is a multi-file rework. TODO marker added in Phase 2; full fix deferred. | Step 10 |
| M6  | `detectors.py:213-275`          | `DetectorThresholds` ↔ `DetectionConfig` unification is a multi-file refactor that touches the orchestrator, ratio defense, and call sites. Loud docstring added in Phase 5; code unification deferred. | Step 10 |
| L3  | `aws/query_builder.py:13-18`    | Endpoint enum extraction belongs in `models.py` and changes the public type surface — out of Step 7 scope.         | Step 10 |
| N2  | (timestamp lex-order note)      | Comment-only nit; not blocking. Add when timestamp formatting is revisited.                                        | Step 10 |
| N3  | (subprocess env in isolation test) | Test currently green; env-isolation works in-place. Re-evaluate if/when the isolation test moves to a CI matrix.    | Future  |

**Note**: M5 and M6 receive light markers in this fix pass (TODO comment +
docstring respectively) so the divergence is discoverable. The actual
behavior change is deferred.

---

## 7. Lesson to Capture (during COMPLETE phase, NOT now)

To be added to `.claude/tasks/lessons.md` during Step 9 (update-docs), not in
this fix pass:

> **When introducing a new config dataclass that overlaps with an existing
> one, write a unifying issue/comment immediately or risk silent semantic
> divergence.** Step 7 introduced `DetectorThresholds` alongside the legacy
> `DetectionConfig`; the new wrappers bypassed the ratio defense in
> `detect_sms_abuse`. Mark divergences loudly in docstrings the moment they
> appear; schedule unification before the next public-API consumer lands.

---

## 8. FIX-AGENT INSTRUCTIONS (sonnet)

**Read this section verbatim. Apply edits phase-by-phase, run the post-flight
checks after each phase, then stop.**

### 8.1 Pre-flight

1. Confirm branch `dev`:
   ```bash
   cd /Users/bhushan/Coding/Projects/OpsMitra && git rev-parse --abbrev-ref HEAD
   ```
2. Confirm target files exist:
   - `src/opsmitra/event_source.py`
   - `src/opsmitra/aws/{athena_source,s3_sink,query_builder}.py`
   - `src/opsmitra/config.py`
   - `src/opsmitra/detectors.py`
   - `tests/test_{athena_source,s3_sink,config,event_source,detectors_with_thresholds}.py`

### 8.2 Edit order (5 phases)

Apply in this exact order. After each phase, run §8.3 quick check.

1. **Phase 1** — N1, N4, N5, L1, L2, L5 (polish).
2. **Phase 2** — M1, M2, M3, M5 (TODO marker), M7 (config bounds + observability).
3. **Phase 3** — H1, H2, H3, M4 (Athena hardening).
4. **Phase 4** — H4, H5 (S3 hardening).
5. **Phase 5** — L4, M6 (docstring only) (detector polish).

### 8.3 Per-phase quick check

```bash
cd /Users/bhushan/Coding/Projects/OpsMitra
ruff check src/ tests/
pytest -q
```

If either fails, stop and report the failing output. Do **not** "fix it harder"
by adding new code paths.

### 8.4 Final post-flight

```bash
cd /Users/bhushan/Coding/Projects/OpsMitra
ruff check src/ tests/
black --check src/ tests/
pytest -q --cov=src --cov-report=term-missing
```

Required outcomes:
- `ruff check`: no errors.
- `black --check`: clean.
- `pytest`: 133 baseline + ~10 new tests all pass.
- `athena_source.py` ≥ 95%, `s3_sink.py` ≥ 95%, `config.py` ≥ 97%,
  `event_source.py` ≥ 92%, suite ≥ 95%.

### 8.5 Stop conditions

After successful post-flight, report:
- The list of files edited per phase.
- The `pytest` summary line (e.g. `143 passed in 0.8s`).
- The coverage % for each of the 4 modules above.
- Do **NOT** commit. Do **NOT** push. Do **NOT** advance to RE-VERIFY (Step 8
  of the workflow). The main session will run RE-VERIFY via the `verify` skill
  next.

---

## 9. Out of Scope for This Fix Pass

- The 5 deferred review findings (see §1.2 and §6).
- Any new features, refactors, or doc updates beyond what is enumerated above.
- The lesson update — that happens in COMPLETE / `update-docs`, not here.
- Coverage uplift beyond the targets in §4.
- Commit / push / PR — those happen only after RE-VERIFY and user confirmation.

---

## 10. Ready-for-FIX Checklist

- [x] All 17 selected findings have a precise file:line target.
- [x] Each finding is grouped into one of 5 phases.
- [x] New tests enumerated (~10, across 4 test files).
- [x] Deferred items listed with one-line rationale (5 items, §6).
- [x] Acceptance bar specifies test count, coverage %, and AC re-check.
- [x] Risks enumerated per phase.
- [x] FIX-agent instructions are sequenced, phase-bounded, and unambiguous.
- [x] Lesson capture is scheduled for COMPLETE phase, not this fix pass.
- [ ] **User confirmation** to proceed to FIX phase (sonnet agent).

**WAITING FOR CONFIRMATION**: Proceed to Step 7 FIX with this plan?
(yes / no / modify)
