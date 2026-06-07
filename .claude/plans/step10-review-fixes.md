# Step 10: Fix Plan — Address Step 10 Review Findings

**Step 10 Plan reference**: [`.claude/plans/step10-security-cost-docs.md`](./step10-security-cost-docs.md)
**Review verdict from Step 10**: **REQUEST CHANGES** (1 CRITICAL)
**Branch**: `dev` (mandatory per `.claude/tasks/lessons.md`)
**Workflow phase**: FIX PLAN (no code in this phase)
**Severity counts**: 1 CRITICAL, 3 HIGH, 4 MEDIUM, 4 LOW, 3 NIT

---

## 1. Scope of This Fix Pass

The Step 10 `python-review` returned **REQUEST CHANGES** with one CRITICAL
correctness regression that silently disables the baseline-ratio defense
in the orchestrator path. Of the 15 findings raised, this plan
**applies 9** (1 CRITICAL + 3 HIGH + 4 MEDIUM + 1 NIT) and **defers 6**
(4 LOW + 2 NIT) with documentation-only follow-ups.

- **1 CRITICAL** — C1 (applied — drop ratio overrides; preserve defense)
- **3 HIGH** — H1, H2, H3 (all applied — CLI wiring + stale docstrings + lessons)
- **4 MEDIUM** — M1, M2, M3, M4 (all applied — docstring, durable lock, mermaid clarity, doc keyword tests)
- **4 LOW** — L1, L2, L3, L4 (all deferred with rationale)
- **3 NIT** — N1 (applied — Protocol type tightening); N2, N3 (deferred)

### 1.1 Findings addressed in this fix pass

| #  | Sev      | File:Line                                | One-line description                                                                                          |
|----|----------|------------------------------------------|---------------------------------------------------------------------------------------------------------------|
| C1 | CRITICAL | `detectors.py:49-58`                     | Drop hard-coded `sms_ratio_threshold=1.0` / `cost_ratio_threshold=1.0` overrides in `_config_from_thresholds`. |
| H1 | HIGH     | `cli.py:217-223`                         | Wire `--config-file` end-to-end (load → resolve → `Runtime(thresholds=…)`) for `run` AND `eval` subcommands.   |
| H2 | HIGH     | `runtime.py:122-123`, `cooldown.py:96-97` | Rewrite two stale "Step 10 deferred" docstrings to describe shipped behavior.                                |
| H3 | HIGH     | `.claude/tasks/lessons.md`               | Update Detector Thresholds "KNOWN DIVERGENCE" entry to reflect Step 10 status (3-clause refresh).               |
| M1 | MEDIUM   | `detectors.py:360-365`                   | Fix `_peak_burst_window` docstring: algorithm returns longest run, not densest slice.                          |
| M2 | MEDIUM   | `cooldown.py:215-235`                    | Move fcntl lock target from per-call tempfile to durable `<cooldown_path>.lock` named lockfile.                |
| M3 | MEDIUM   | `docs/architecture.md` (D2 sequenceDiagram) | Split init-time vs execute-time validation into two distinct `Note over` blocks; cite `RuntimeConfigurationError`. |
| M4 | MEDIUM   | `tests/test_docs.py`                     | Add `test_docs_contain_required_keywords` — one keyword-content assertion per critical doc.                    |
| N1 | NIT      | `runtime.py:155`                         | `fallback_summarizer: Any = None` → `_SummarizerProto | None = None` (consistency with Step 8 L5 Protocol).    |

### 1.2 Findings explicitly DEFERRED (documentation-only this pass)

| #  | Sev   | One-line reason for deferral                                                                                       | Doc action this pass |
|----|-------|--------------------------------------------------------------------------------------------------------------------|----------------------|
| L1 | LOW   | `__main__.py` 0% coverage — `# pragma: no cover` already in place; cosmetic artifact only.                          | No action |
| L2 | LOW   | `cli.py` 87% live-mode branches — acceptable for v0 final step; no AWS creds in CI.                                  | No action |
| L3 | LOW   | `aws-window-replay-findings.md` template uses 5 sections vs plan §7.2's 8 — intentional usability contraction.       | One-line rationale appended to §6 deferral table |
| L4 | LOW   | `query_builder.py` `_ALLOWED_ENDPOINTS = EVENT_TYPES` back-compat alias — harmless shim.                              | No action |
| N2 | NIT   | `cooldown.py:222` catches only `BlockingIOError`; adding `OSError` catches more failure modes but adds noise.        | No action |
| N3 | NIT   | README env-var table omits `confidence_floor` / `baseline_ratio` — those fields don't exist on `DetectorThresholds` and won't after C1 fix. | No action (resolved by C1) |

See §6 for the full deferral table.

---

## 2. Phased Fix Sequence

Edits are grouped low-risk → high-risk so each phase is small, reviewable,
and verifiable before the next begins. No phase touches plan/build files
beyond `.claude/tasks/lessons.md` (H3) and `docs/architecture.md` (M3).

### Phase 1 — Zero-risk docstring / type / lessons polish (no behavior change)

Targets: **M1 (docstring), H2 (two docstrings), N1 (type), H3 (lessons.md update)**.

- **M1** — `detectors.py:360-365`: rewrite the `_peak_burst_window`
  docstring to describe what the algorithm actually returns:
  ```
  """Return the longest run of events that all fall within window_seconds of each other.

  Uses a two-pointer sliding window over the sorted event list. The returned
  list is the largest *count* of consecutive events whose timestamp span is
  <= window_seconds. (Not the "densest" slice — peak-density would require a
  different algorithm; the existing test pins the longest-run semantic.)
  """
  ```
  No code change. Existing `test_peak_burst_window_*` tests continue to pass.
- **H2a** — `runtime.py:122-123` (`Runtime.__init__` docstring,
  `thresholds:` arg): replace the current text
  `"DetectorThresholds (passed for API completeness; wiring into detect_anomalies deferred to Step 10 — see plan §3)"`
  with:
  `"DetectorThresholds. Forwarded to detect_anomalies as thresholds=…; the public DetectorThresholds knobs (sms_abuse_rate_per_minute, auth_burst_count, endpoint_error_rate, endpoint_error_min_samples, cost_runaway_delta_usd, *_window_seconds) tune the absolute-threshold sub-detectors. The baseline-ratio defense (5x SMS, 10x cost) is preserved at DetectionConfig defaults and is NOT tunable via DetectorThresholds in v0."`
- **H2b** — `cooldown.py:96-97` (`AnomalyCooldown` class docstring,
  `WARNING:` paragraph): replace the current text
  `"WARNING: single-process backend; concurrent Runtimes will race on persist (Step 10 will swap for DynamoDB/fcntl)."`
  with:
  `"NOTE: single-host backend with a durable named lockfile at <path>.lock. Concurrent processes on the same host are serialised by POSIX advisory flock; a writer that loses the lock race logs a WARNING and skips persist (cooldown is performance, not correctness). For multi-host stateless deployments (e.g. Lambda), swap for DynamoDB/Redis — the AnomalyCooldown interface is unchanged."`
- **N1** — `runtime.py:155` (`Runtime.__init__` signature):
  `fallback_summarizer: Any = None` → `fallback_summarizer: _SummarizerProto | None = None`.
  - Verify `_SummarizerProto` is already imported at module scope (Step 8 L5
    introduced it); if so, no new import. If imported under
    `if TYPE_CHECKING:`, leave it there — `_SummarizerProto | None` is fine
    in a runtime annotation under `from __future__ import annotations`
    (which `runtime.py` already uses).
  - Runtime behaviour unchanged — Protocol is structural; `FallbackSummarizer`
    instance and `None` both satisfy the annotated type.
- **H3** — `.claude/tasks/lessons.md`: locate the Detector Thresholds
  "KNOWN DIVERGENCE" entry. Rewrite it (preserving heading) to a
  three-clause status update:
  > **STATUS (Step 10 shipped):**
  > 1. The orchestrator path now accepts `DetectorThresholds` via
  >    `Runtime(thresholds=…)`; thresholds flow into `detect_anomalies` via
  >    `_config_from_thresholds(...)` for the per-window mapping.
  > 2. The baseline-ratio defense (5x SMS, 10x cost) is preserved at
  >    `DetectionConfig` defaults. It is **not tunable** via
  >    `DetectorThresholds` in v0 — an acceptable trade-off documented here.
  > 3. The public `detect_*` wrappers (`detect_sms_abuse`, `detect_cost_runaway`,
  >    etc.) still bypass the ratio defense and remain an anti-pattern for
  >    library callers. **Do NOT mix wrapper calls with the orchestrator
  >    on the same window.** Use `detect_anomalies(...)` end-to-end.

  Add this lessons.md update as part of Phase 1 (not Phase 9 / COMPLETE)
  because it is a semantic clarification load-bearing for the fix pass.

**Verifier**: `.venv/bin/ruff check src/ tests/`, `.venv/bin/black --check src/ tests/`,
full suite green. Zero behavior change expected. The `_SummarizerProto`
annotation change is detected at type-check time only; runtime is unchanged.

### Phase 2 — Test hardening (M4 documentation keyword coverage)

Targets: **M4**.

- **M4** — `tests/test_docs.py`: add one test that asserts critical
  documentation content has not silently regressed:
  ```python
  def test_docs_contain_required_keywords():
      """Smoke-check that critical docs retain expected content keywords."""
      repo_root = Path(__file__).parent.parent
      checks = [
          ("docs/security-checklist.md",   "IAM policy"),
          ("docs/aws-cost-controls.md",    "BytesScannedCutoffPerQuery"),
          ("docs/architecture.md",         "detect_anomalies"),
          ("docs/architecture.md",         "Cooldown"),
      ]
      for rel_path, keyword in checks:
          doc_path = repo_root / rel_path
          assert doc_path.exists(), f"{rel_path} missing"
          text = doc_path.read_text(encoding="utf-8")
          assert keyword in text, (
              f"{rel_path} must contain {keyword!r}; got first 200 chars: {text[:200]!r}"
          )
  ```
- The existing presence-only tests in `tests/test_docs.py` remain — this
  test layers content-content checks on top.
- The `"detect_anomalies"` and `"Cooldown"` keywords are guaranteed to
  hold after M3 (Phase 6 mermaid diagram update) lands; verify the
  ordering — M4 expects the M3 keywords to be in place. If Phase 2 runs
  before M3, the test will fail; **Phase 2 must reference the keywords
  that already exist pre-M3** (`detect_anomalies` and `Cooldown` already
  appear in the current architecture.md per the review citation —
  verify before edit by grep).

**Verifier**: new test passes against current `docs/architecture.md`.
If grep shows either `detect_anomalies` or `Cooldown` absent today,
defer those two assertions until after Phase 6.

### Phase 3 — CRITICAL correctness: preserve baseline-ratio defense (C1)

Targets: **C1** + 2 existing test updates + 1 new test.

- **C1** — `detectors.py:49-58` (`_config_from_thresholds` return value):
  remove the two `=1.0` overrides. Today:
  ```python
  return DetectionConfig(
      sms_min_count=sms_min_count,
      sms_ratio_threshold=1.0,            # <-- DROP
      auth_min_failures=thresholds.auth_burst_count,
      auth_min_users=1,
      auth_burst_window_seconds=thresholds.auth_burst_window_seconds,
      error_min_requests=thresholds.endpoint_error_min_samples,
      error_rate_threshold=thresholds.endpoint_error_rate,
      cost_min_units=thresholds.cost_runaway_delta_usd,
      cost_ratio_threshold=1.0,           # <-- DROP
      cost_runaway_window_seconds=thresholds.cost_runaway_window_seconds,
  )
  ```
  After fix:
  ```python
  return DetectionConfig(
      sms_min_count=sms_min_count,
      # sms_ratio_threshold intentionally NOT overridden — DetectionConfig default (5.0)
      # preserves the baseline-ratio defense. DetectorThresholds does NOT tune ratios in v0.
      auth_min_failures=thresholds.auth_burst_count,
      auth_min_users=1,
      auth_burst_window_seconds=thresholds.auth_burst_window_seconds,
      error_min_requests=thresholds.endpoint_error_min_samples,
      error_rate_threshold=thresholds.endpoint_error_rate,
      cost_min_units=thresholds.cost_runaway_delta_usd,
      # cost_ratio_threshold intentionally NOT overridden — DetectionConfig default (10.0)
      # preserves the baseline-ratio defense. DetectorThresholds does NOT tune ratios in v0.
      cost_runaway_window_seconds=thresholds.cost_runaway_window_seconds,
  )
  ```
  Net behaviour: when `Runtime(thresholds=…)` is set, the resulting
  `DetectionConfig` now keeps `sms_ratio_threshold=5.0` and
  `cost_ratio_threshold=10.0` — so a tenant must exceed BOTH the absolute
  threshold and the baseline ratio to trigger an SMS/cost anomaly,
  matching the pre-Step 10 orchestrator behaviour. This closes the
  silent-regression CRITICAL.

- **Existing test updates (2)** — the two M6 tests in
  `tests/test_detectors_with_thresholds.py` currently rely on the
  ratio-override side effect to flip detection. After C1, they must
  exercise the absolute-threshold knob directly:
  - `test_detect_anomalies_uses_threshold_overrides` — change to use
    `sms_abuse_rate_per_minute=999` (very high) to confirm the threshold
    suppresses what would otherwise be detected; or
    `sms_abuse_rate_per_minute=0.1` (very low) to confirm a low threshold
    surfaces small bursts.
  - `test_detect_anomalies_threshold_override_applied_per_field` — same
    pattern, but per-field: tune `auth_burst_count`, `endpoint_error_rate`,
    `cost_runaway_delta_usd` to verify each knob individually. The ratio
    fields are no longer in scope.

- **New test (1)** —
  `test_runtime_thresholds_preserve_baseline_ratio_defense` in
  `tests/test_runtime.py`:
  - Build a fixture where baseline cost = $100 and current-window cost = $200
    (only 2x baseline, well under the 10x ratio defense). Even though
    absolute `cost_runaway_delta_usd` would be exceeded (e.g. cost diff = $100,
    threshold = $50), the ratio defense (10x) must still suppress.
  - Construct `Runtime(thresholds=DetectorThresholds(cost_runaway_delta_usd=50.0, …))`
    and call `runtime.execute(window_start, window_end, …)`.
  - Assert the resulting `RuntimeResult.anomalies_detected == 0` for cost.
  - Companion assertion for SMS: baseline = 100, current = 200 (2x);
    `sms_abuse_rate_per_minute` very low; ratio defense (5x) must suppress.
  - This is the authoritative test that C1 closes the regression.

**Verifier**: new test passes; the two updated M6 tests pass after
re-tuning to use absolute-threshold knobs rather than ratio overrides.

### Phase 4 — Durable cooldown lock (M2)

Targets: **M2** + optional update to `test_concurrent_writers_second_logs_warning_and_skips_persist`.

- **M2** — `cooldown.py:215-235` (`persist()` lock acquisition): change
  the lock target from the per-call tempfile to a stable named lockfile.
  Today the lock fd opens `tmp_path` (a fresh path every call) → two
  concurrent writers each get unique tempfiles → flock never collides →
  zero mutual exclusion.
  After fix:
  ```python
  # Acquire exclusive advisory lock on a durable named lockfile next to the
  # cooldown JSON. The same lock path across processes ensures flock(LOCK_NB)
  # actually serialises writers. Cooldown is perf, not correctness — losers
  # log and skip.
  lock_path = str(path) + ".lock"
  lock_fd: int | None = None
  if _LOCK_SUPPORTED:
      try:
          lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
          fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
      except BlockingIOError:
          logger.warning("cooldown persist skipped: another writer holds the lock")
          if lock_fd is not None:
              try:
                  os.close(lock_fd)
              except OSError:
                  pass
          return
  ```
  The `try / finally` block that follows must close `lock_fd` on every
  path (success, exception, or no-op) — keep the existing finally; only
  the acquisition target changes.
- **Tempfile flow unchanged** — `mkstemp(dir=str(dir_), suffix=".tmp")`
  still creates the data tempfile; the rename to `path` still happens
  inside the lock. The lock is now a *separate* durable file.
- The `<path>.lock` file is created with `0o600` to match the cooldown
  JSON's permissions discipline. It is *not* unlinked between calls —
  the file persists across runs, but only the flock state matters.
- **Test update** — `tests/test_cooldown.py`
  `test_concurrent_writers_second_logs_warning_and_skips_persist`:
  if the test monkeypatches `tempfile.mkstemp` to simulate a lock
  collision on the tempfile path, that mock is no longer load-bearing —
  the lock is now on `<path>.lock`. Refactor the test to:
  - Pre-open `<cooldown_path>.lock` with `os.open(..., O_CREAT | O_RDWR)`
    + `fcntl.flock(LOCK_EX | LOCK_NB)` in the test setup (simulating
    "another writer holds the lock").
  - Construct an `AnomalyCooldown`, record an alert, call `.persist()`.
  - Assert the call returns without raising (skip path).
  - Assert the logger emitted the WARNING.
  - Assert the cooldown JSON file was NOT updated (the previous on-disk
    state, or absence, is preserved).
  - Finally, release the test-held lock + close the fd in teardown.
- **fd-leak audit**: verify (by reading) that every path through
  `persist()` (acquisition skip, write success, write exception)
  closes `lock_fd`. The finally clause covers success + exception;
  the explicit close inside `except BlockingIOError` covers the skip
  path. No other paths exist.

**Verifier**: updated test passes; new behaviour is real mutual exclusion
verified via two concurrent processes (simulated by holding the lock in
the test). `lsof` / fd-counter check optional.

### Phase 5 — CLI `--config-file` end-to-end wiring (H1)

Targets: **H1** + 1 new test.

- **H1** — `cli.py:217-223` (the WARN-and-skip block in `_compose_and_execute`):
  remove the print + WARN and replace with full threshold resolution.
  After fix (sketch, mirrors `_load_thresholds` patterns in `config.py`):
  ```python
  # Build thresholds: start with overrides (env / CLI flags / config file),
  # then resolve per-tenant / per-endpoint via resolve_thresholds(…).
  from opsmitra.config import (
      DetectorThresholds,
      load_thresholds,
      resolve_thresholds,
  )
  base_overrides: dict[str, Any] = {}
  config_file_path = getattr(args, "config_file", None)
  if config_file_path:
      base_overrides = load_thresholds(Path(config_file_path))
  thresholds = resolve_thresholds(
      base_overrides,
      tenant=getattr(args, "tenant", None),
      endpoint=getattr(args, "types", [None])[0] if getattr(args, "types", None) else None,
  )
  ```
  Notes:
  - Exact signatures (`load_thresholds(path) -> dict | DetectorThresholds`,
    `resolve_thresholds(...)`) must be re-checked against the current
    `config.py` implementation during FIX. If `load_thresholds` returns
    a `DetectorThresholds` directly (no `resolve_thresholds` step), the
    sketch collapses to `thresholds = load_thresholds(Path(config_file_path))`
    when the file is provided, else `DetectorThresholds()`. The fix
    must match whatever `config.py` actually exports — do not introduce
    a new public API here.
  - Tenant / endpoint resolution: if `resolve_thresholds` does not exist
    yet for v0, fall back to `load_thresholds(path)` only; document the
    deferral inline as `# TODO(post-v0): per-tenant resolution`.
- **Mirror in `eval`** — `_cmd_eval` (or wherever the evaluation harness
  constructs its `Runtime`): apply the same `--config-file → thresholds`
  resolution so `run` and `eval` are parity. If `eval` does not currently
  expose `--config-file`, add the argparse flag and thread it through.
- **Remove the WARN** — delete the stderr print at lines 219-223. No
  user-visible behaviour change beyond "the override is now honored".
- **New test (1)** — `test_cli_run_config_file_threshold_override_honored`
  in `tests/test_cli.py`:
  - Write a minimal thresholds JSON/YAML to `tmp_path` with one
    distinctive field (e.g. `sms_abuse_rate_per_minute: 0.5`).
  - Invoke `opsmitra run --config-file <path> --window-start … --window-end … --dry-run`
    against a fixture event source that, with default thresholds, produces
    0 anomalies, but with the low threshold, produces ≥1 SMS anomaly.
  - Assert exit code 2 (anomalies detected) and the anomaly count > 0.
  - Companion negative: omitting `--config-file` against the same fixture
    yields exit code 0.
  - Optionally add a parallel `test_cli_eval_config_file_threshold_override_honored`
    if the `eval` mirror is non-trivial; otherwise rely on the `run` test.

**Verifier**: new test passes; existing CLI tests pass; `grep -rn
"do not yet reach detect_anomalies" src/` returns empty.

### Phase 6 — Mermaid diagram clarity (M3)

Targets: **M3**.

- **M3** — `docs/architecture.md` D2 sequenceDiagram: today the diagram
  conflates `__init__` config validation (Step 8 H2 Stage 1 — guards
  `dry_run=False` with missing webhook) with `execute()` window validation
  (window_start < window_end, etc.). Split into two explicit blocks:
  ```mermaid
  sequenceDiagram
      participant CLI
      participant Runtime
      participant Detect as detect_anomalies
      participant Cooldown
      Note over Runtime: __init__: validate alerter config<br/>raises RuntimeConfigurationError<br/>if dry_run=False and webhook missing
      CLI->>Runtime: Runtime(source, alerter, cooldown, thresholds, dry_run=…)
      Note over Runtime: execute(window_start, window_end): validate window<br/>raises ValueError if start >= end
      CLI->>Runtime: execute(window_start, window_end, tenant, types)
      Runtime->>Detect: detect_anomalies(events, ws, we, thresholds=…)
      Detect-->>Runtime: list[Anomaly]
      Runtime->>Cooldown: should_alert(anomaly) / record_alerted
      Runtime-->>CLI: RuntimeResult
  ```
- Two distinct `Note over` blocks make the two-stage validation explicit;
  the diagram now matches `Runtime.__init__` source.
- The text around the diagram (prose paragraph) should be updated to
  mention `RuntimeConfigurationError` by name and link to `runtime.py`.
- M4 (Phase 2) already asserts `"detect_anomalies"` and `"Cooldown"` are
  present in `architecture.md`; this phase preserves both keywords.

**Verifier**: M4 keyword test (Phase 2) still passes; `mermaid-cli`
(if installed) or VS Code preview renders cleanly. No automated mermaid
syntax check is required — the test asserts text-keyword presence only.

---

## 3. New & Updated Tests — Consolidated List

Three strictly required new tests; two existing tests updated for C1.

### 3.1 `tests/test_runtime.py` — +1 test (C1)

1. **`test_runtime_thresholds_preserve_baseline_ratio_defense`** —
   - Build an event fixture: baseline window cost = $100, current window
     cost = $200 (2x ratio). Set `DetectorThresholds(cost_runaway_delta_usd=50.0)`
     so the absolute threshold would be exceeded (Δ = $100 > $50).
   - The 10x ratio defense (preserved by C1) must suppress: 2x < 10x.
   - Construct `Runtime(thresholds=…, dry_run=True)`; call `execute(…)`.
   - Assert no cost anomaly in `RuntimeResult.anomalies_detected`.
   - Companion SMS sub-case: baseline SMS rate = 100/min, current = 200/min
     (2x); `sms_abuse_rate_per_minute=10.0`; the 5x ratio defense suppresses.
   - Assert no SMS anomaly.

### 3.2 `tests/test_cli.py` — +1 test (H1)

2. **`test_cli_run_config_file_threshold_override_honored`** —
   - Write `tmp_path/thresholds.json` with `{"sms_abuse_rate_per_minute": 0.5}`.
   - Write a local-mode event fixture to `tmp_path/events.jsonl` with
     ~50 SMS events in a 1-minute window (rate = 50/min, well above the
     0.5/min threshold; baseline absent so ratio defense doesn't gate).
   - Invoke `python -m opsmitra run --config-file tmp_path/thresholds.json
     --window-start … --window-end … --dry-run`.
   - Assert exit code 2 (anomalies detected); assert stdout contains
     the SMS anomaly summary.
   - Companion: omit `--config-file`; default `sms_abuse_rate_per_minute`
     yields exit 0.

### 3.3 `tests/test_docs.py` — +1 test (M4)

3. **`test_docs_contain_required_keywords`** — see Phase 2 above.

### 3.4 Updated tests (in-place body changes only)

- `tests/test_detectors_with_thresholds.py`:
  - `test_detect_anomalies_uses_threshold_overrides` — re-tune to use
    `sms_abuse_rate_per_minute=999` (high suppress) or `0.1` (low surface);
    do NOT rely on ratio override.
  - `test_detect_anomalies_threshold_override_applied_per_field` — tune
    per-field absolute thresholds; drop any assumptions about ratio=1.0.
- `tests/test_cooldown.py`:
  - `test_concurrent_writers_second_logs_warning_and_skips_persist` — refactor
    to hold the `<path>.lock` lockfile in the test, then attempt a concurrent
    persist. If the test no longer needs to monkeypatch `tempfile.mkstemp`,
    simplify by removing the monkeypatch.

### 3.5 No new tests required for

- **H2** (docstring rewrites — text only), **H3** (lessons.md — text only),
  **M1** (`_peak_burst_window` docstring — algorithm unchanged, existing
  tests cover), **M3** (mermaid diagram — M4 keyword test covers presence),
  **N1** (Protocol annotation tightening — runtime behaviour unchanged;
  Protocol is structural, existing tests with `Mock` collaborators
  continue to satisfy).

---

## 4. Acceptance

After the FIX phase runs and RE-VERIFY completes:

1. **All 4 Step 10 acceptance criteria still PASS** (re-verified per
   `.claude/plans/step10-security-cost-docs.md` §1):
   - AC1 — `opsmitra run` end-to-end smoke against fixtures: clean exit 0,
     anomaly-present exit 2 (no regression).
   - AC2 — `opsmitra eval` strict + non-strict semantics preserved (no
     regression from H1 wiring).
   - AC3 — Cooldown suppression unchanged behaviourally; durable lock
     (M2) adds real mutual exclusion without changing the public API.
   - AC4 — Security checklist + AWS cost-control docs reachable; M4
     keyword test asserts critical content present.
2. **Test suite green**:
   - Existing **246 tests** + **3 new** = **≥ 249 passing** (some existing
     tests updated in-place; net delta is +3 new minus 0 removed).
   - Tolerance: +/-1 for the optional `eval` mirror test in §3.2.
3. **Coverage floors** (matching or exceeding Step 10 baseline):
   - `src/opsmitra/detectors.py` ≥ **96%**
   - `src/opsmitra/runtime.py` ≥ **90%**
   - `src/opsmitra/cooldown.py` ≥ **90%**
   - `src/opsmitra/cli.py` ≥ **84%**
   - Suite total ≥ **94%**
4. **No regression on Steps 1–9** — all baseline tests from prior steps
   remain green.
5. **`ruff` + `black` clean** on `src/` and `tests/`. `ruff F401` reports
   zero unused imports.

---

## 5. Risks

| Phase | Risk | Mitigation |
|------|------|------------|
| 1 | H2 docstring rewrites accidentally drift from actual behaviour (e.g. claim ratio defense is preserved while C1 fix lands separately). | Phase 1 docstrings describe POST-C1 state; Phase 3 lands C1 on top. If Phase 3 is skipped for any reason, Phase 1 docstrings become incorrect. Re-verify after Phase 3. |
| 1 | N1 Protocol annotation rejects an existing test fake that doesn't expose `summarize(...)`. | `_SummarizerProto` is `@runtime_checkable`; `MagicMock` / `Mock(spec=…)` satisfy at runtime. Pre-edit grep for non-Mock fakes in `tests/`. |
| 1 | H3 lessons.md rewrite collides with concurrent edits on `dev`. | One-shot append; sequence after all source edits land. |
| 2 | M4 keyword test fails because `"detect_anomalies"` or `"Cooldown"` keyword absent from current `architecture.md` (pre-M3). | Pre-edit grep `docs/architecture.md` for both keywords; if missing, gate those assertions until after Phase 6 (M3 update). |
| 3 | **C1** behaviour shift causes existing M6 tests to flip pass→fail. | Pre-edit grep for tests asserting on ratio behaviour; update the two M6 tests in the same phase. New `test_runtime_thresholds_preserve_baseline_ratio_defense` is the authoritative coverage. |
| 3 | C1 changes the public observable of `_config_from_thresholds` in a way that breaks an external caller. | `_config_from_thresholds` is module-private (underscore prefix). No public API change. Direct callers exist only inside `detectors.py` and tests. |
| 4 | M2 durable lockfile leaks `0o600` file into a directory the user didn't expect. | Lockfile sits next to `cooldown.json` (same dir, same caller intent); permissions match the JSON. Document in `cooldown.py` module docstring. |
| 4 | M2 test refactor for `test_concurrent_writers_*` deadlocks itself (test holds the lock, persist tries to acquire). | The test holds an EXCLUSIVE lock on `<path>.lock`; `persist()` uses `LOCK_NB` so it returns `BlockingIOError` immediately rather than deadlocking. Verify by single-threaded test run. |
| 5 | H1 `load_thresholds` / `resolve_thresholds` signature in `config.py` does not match the sketch. | FIX-agent verifies signatures via `grep "def load_thresholds\|def resolve_thresholds" src/opsmitra/config.py` before edit; adjust call sites to match actual signatures. No new public API. |
| 5 | H1 `eval` subcommand does not currently accept `--config-file`; adding the flag could collide with an existing arg. | Pre-edit grep `tests/test_cli.py` and `_cmd_eval` argparse for collisions; namespace the flag with the same name as `run` for parity. |
| 6 | M3 mermaid syntax error breaks the markdown renderer on GitHub. | Mermaid blocks render lazily; M4 keyword test still passes even if rendering fails. Manual verify in GitHub preview after merge. |

---

## 6. Deferred Items (post-v0 hardening candidates)

| ID  | Item                                                              | Why deferred                                                                                                  | Target |
|-----|-------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|--------|
| L1  | `__main__.py` 0% coverage                                          | `# pragma: no cover` already in place (Step 9 N2). Cosmetic coverage artifact only — line is exercised by subprocess test, just invisible to in-process coverage. | (closed via pragma) |
| L2  | `cli.py` 87% live-mode branches                                    | Live AWS / Slack paths require credentials not present in CI. Acceptable for v0 final step; floors honoured at ≥ 84%. | post-v0 |
| L3  | `aws-window-replay-findings.md` template: 5 sections vs plan §7.2's 8 | Template intentionally simplified to 5 sections for usability; user can extend post-replay with additional sections as needed. Documented divergence from plan, not a regression. | (closed — usability decision) |
| L4  | `query_builder.py` `_ALLOWED_ENDPOINTS = EVENT_TYPES` alias        | Back-compat shim for callers that imported `_ALLOWED_ENDPOINTS` directly. Harmless; removal would be a breaking change for zero current external benefit. | post-v0 |
| N2  | `cooldown.py:222` `except BlockingIOError` widening to `OSError`   | `OSError` would catch e.g. `PermissionError`, `OSError(EAGAIN)` on weird FS; widens for low realistic gain in v0. Cooldown is perf, not correctness — silent skip on edge cases is acceptable. | post-v0 |
| N3  | README env-var table missing `confidence_floor`/`baseline_ratio`    | These fields **do not exist** on `DetectorThresholds` and will not exist after C1 lands. The "missing" entries reflect that ratios are NOT user-tunable in v0. (Resolved by C1.) | (closed by C1) |

**Note**: L1, L3, N3 are closed in-place (pragma / usability rationale / C1
resolution). L2, L4, N2 remain as post-v0 hardening candidates with no
action this pass.

---

## 7. Lessons to Capture (during this fix pass — Phase 1)

Two new lessons are added to `.claude/tasks/lessons.md` as part of Phase 1
(alongside the H3 status update to the Detector Thresholds entry).
These are **load-bearing for the fix pass**, not deferred to COMPLETE,
because they directly justify the C1 design choice and the M2 durability
fix.

> **When unifying parallel APIs, the "weaker" API's defaults must not
> silently leak into the "stronger" path.** Setting `sms_ratio_threshold=1.0`
> and `cost_ratio_threshold=1.0` in `_config_from_thresholds` disabled the
> baseline-ratio defense that the orchestrator path was meant to PRESERVE.
> Default-preserving identity mappings beat opinionated re-mappings.
> Lesson: when bridging API A → API B, only forward the fields that A
> explicitly exposes; let B's defaults stand for everything else.
> *(Justifies C1 fix.)*

> **`fcntl` advisory locks must target a stable, named lockfile, not a
> per-call temp path.** A lockfile created by `tempfile.mkstemp` is unique
> per call, so two concurrent writers never collide on it → the flock is
> a no-op. The lock target must be a durable named file (`<path>.lock`)
> co-located with the protected resource. Verify mutual exclusion by
> simulating a concurrent lock-holder in the test, not by trusting the
> `flock` call alone. *(Justifies M2 fix.)*

H3 (Detector Thresholds "KNOWN DIVERGENCE" → "STATUS (Step 10 shipped)"
rewrite) is the third lessons.md edit this pass; see Phase 1.

---

## 8. FIX-AGENT INSTRUCTIONS (sonnet)

**Read this section verbatim. Apply edits phase-by-phase, run the
post-flight check after each phase, then stop.**

### 8.1 Pre-flight

1. Confirm branch `dev`:
   ```
   cd /Users/bhushan/Coding/Projects/OpsMitra && git rev-parse --abbrev-ref HEAD
   ```
2. Confirm target files exist:
   - `src/opsmitra/detectors.py`, `runtime.py`, `cooldown.py`, `cli.py`, `config.py`
   - `tests/test_detectors_with_thresholds.py`, `test_runtime.py`,
     `test_cooldown.py`, `test_cli.py`, `test_docs.py`
   - `docs/architecture.md`, `docs/security-checklist.md`, `docs/aws-cost-controls.md`
   - `.claude/tasks/lessons.md`
3. Pre-edit greps:
   - `grep -n "ratio_threshold=1.0" src/opsmitra/detectors.py` (expect 2 hits — C1 targets).
   - `grep -n "Step 10" src/opsmitra/runtime.py src/opsmitra/cooldown.py`
     (expect 2 stale references — H2a, H2b).
   - `grep -n "do not yet reach" src/opsmitra/cli.py` (expect 1 hit — H1 target).
   - `grep -n "densest" src/opsmitra/detectors.py` (expect 1 hit — M1).
   - `grep -n "detect_anomalies\|Cooldown" docs/architecture.md` (must be > 0;
     if zero, Phase 2 M4 must defer those two assertions until after Phase 6).
   - `grep -n "fallback_summarizer: Any" src/opsmitra/runtime.py` (expect 1 hit — N1).
4. Verify `.venv/bin/python` and `.venv/bin/pytest` exist (project convention).

### 8.2 Edit order (6 phases)

Apply in this exact order. After each phase, run §8.3 quick check.

1. **Phase 1** — M1 (docstring), H2a (`runtime.py` docstring),
   H2b (`cooldown.py` docstring), N1 (Protocol type), H3 (lessons.md
   "KNOWN DIVERGENCE" rewrite + 2 new lessons appended).
2. **Phase 2** — M4 (`test_docs_contain_required_keywords` in
   `tests/test_docs.py`).
3. **Phase 3** — C1 (drop ratio overrides in `_config_from_thresholds`) +
   update 2 existing M6 tests in `test_detectors_with_thresholds.py` +
   add `test_runtime_thresholds_preserve_baseline_ratio_defense`.
4. **Phase 4** — M2 (durable lock target `<path>.lock`) + refactor
   `test_concurrent_writers_second_logs_warning_and_skips_persist`.
5. **Phase 5** — H1 (wire `--config-file` in `run` + `eval`) + new
   `test_cli_run_config_file_threshold_override_honored`.
6. **Phase 6** — M3 (split D2 sequenceDiagram in `docs/architecture.md`).

### 8.3 Per-phase quick check

```
cd /Users/bhushan/Coding/Projects/OpsMitra
.venv/bin/ruff check src/ tests/
.venv/bin/pytest -q
```

If either fails, stop and report the failing output. Do **not** "fix it
harder" by adding new code paths.

### 8.4 Final post-flight

```
cd /Users/bhushan/Coding/Projects/OpsMitra
.venv/bin/ruff check src/ tests/
.venv/bin/black --check src/ tests/
.venv/bin/pytest -q --cov=src --cov-report=term-missing
```

Required outcomes:
- `ruff check`: no errors, zero F401.
- `black --check`: clean.
- `pytest`: ≥ 249 passing (246 baseline + 3 net new; some updates in place).
- `detectors.py` ≥ 96%, `runtime.py` ≥ 90%, `cooldown.py` ≥ 90%,
  `cli.py` ≥ 84%, suite ≥ 94%.

### 8.5 Stop conditions

After successful post-flight, report:
- The list of files edited per phase.
- The `.venv/bin/pytest` summary line.
- The coverage % for `detectors.py`, `runtime.py`, `cooldown.py`,
  `cli.py`, and the suite total.
- Do **NOT** commit. Do **NOT** push. Do **NOT** advance to RE-VERIFY.
  The main session will run RE-VERIFY via the `verify` skill next.

---

## 9. Out of Scope for This Fix Pass

- The 6 deferred review findings (4 LOW + 2 NIT — see §1.2 and §6); only
  documentation/comment markers (or no action) are applied this pass.
- Any new features, refactors, or doc updates beyond what is enumerated
  above (e.g. no DynamoDB cooldown backend, no per-tenant `resolve_thresholds`
  if not already present in `config.py`, no new evaluation metrics).
- Coverage uplift beyond the floors in §4.
- Commit / push / PR — those happen only after RE-VERIFY and user
  confirmation.

---

## 10. Ready-for-FIX Checklist

- [x] All 9 applied findings have a precise file:line target.
- [x] Each finding is grouped into one of 6 phases.
- [x] New tests enumerated (3 required: C1, H1, M4).
- [x] Updated tests enumerated (2 M6 tests + 1 cooldown test).
- [x] Deferred items listed with rationale (6 items, §6).
- [x] Acceptance bar specifies test count, coverage floors, and AC re-check.
- [x] Risks enumerated per phase.
- [x] FIX-agent instructions are sequenced, phase-bounded, and unambiguous.
- [x] Two new lessons captured *in this fix pass* (Phase 1) + H3 rewrite
      of the Detector Thresholds entry.
- [x] `.venv/bin/pytest` and `.venv/bin/python` used in all commands
      per project convention.
- [x] **Blanket approval acknowledged**: proceed to Step 10 FIX with
      this plan; no wait required.
