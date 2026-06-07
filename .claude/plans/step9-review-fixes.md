# Step 9: Fix Plan — Address Step 9 Review Findings

**Step 9 Plan reference**: [`.claude/plans/step9-evaluation-harness.md`](./step9-evaluation-harness.md)
**Review verdict from Step 9**: **REQUEST CHANGES** (no CRITICAL)
**Branch**: `dev` (mandatory per `.claude/tasks/lessons.md`)
**Workflow phase**: FIX PLAN (no code in this phase)
**Severity counts**: 3 HIGH, 4 MEDIUM, 2 LOW, 2 NIT, 0 CRITICAL

---

## 1. Scope of This Fix Pass

The Step 9 `python-review` returned **REQUEST CHANGES**. Of the 11 findings
raised, this plan **applies 9** and **defers 2** with documentation-only
follow-ups. No CRITICAL exists.

- **3 HIGH** — H1, H2, H3 (all applied — FP determinism + fixture cap + strict semantics)
- **4 MEDIUM** — M1, M2, M3 (applied); **M4 deferred with TODO comment**
- **2 LOW** — L1 (applied); **L2 applied as docstring only (structural cause fixed by H1)**
- **2 NIT** — N1, N2 (both applied)

### 1.1 Findings addressed in this fix pass

| #  | Sev   | File:Line                            | One-line description                                                                                       |
|----|-------|--------------------------------------|------------------------------------------------------------------------------------------------------------|
| H1 | HIGH  | `evaluation.py:~302`                 | Sort anomalies deterministically before greedy bipartite match; document tiebreak in `evaluate_case`.       |
| H2 | HIGH  | `evaluation.py:load_case`            | Add `_MAX_EVENTS_FILE_BYTES = 1_000_000` cap; stat-and-raise `ValueError` before reading events file.      |
| H3 | HIGH  | `cli.py:_cmd_eval`                   | Strict mode fails ONLY on `must_detect` (critical) misses; non-strict soft-fails (exit 2) on any miss.    |
| M1 | MED   | `evaluation.py:evaluate_case/_all`   | Remove documented-but-dead `runtime_factory` parameter from public signatures.                              |
| M2 | MED   | `evaluation.py` + tests              | Rename `detection_delays_seconds` → `detection_delay_upper_bound_seconds`; update p50/p95 column headers.   |
| M3 | MED   | `evaluation.py:_summarize_delays`    | Switch p95 to nearest-rank via `statistics.quantiles(...)` when `n ≥ 100`; document empirical for smaller. |
| L1 | LOW   | `evaluation.py:_normalize_subject_dict` | Add `TypeError` guard for non-primitive subject values (str/int/float/bool/None only).                  |
| L2 | LOW   | `evaluation.py:_match_expecteds_to_anomalies` | Add docstring noting greedy-in-expected-order tiebreak; structural cause closed by H1.              |
| N1 | NIT   | `evaluation.py` imports              | Drop the unused `from dataclasses import ...` namespace import if present; keep only used names.            |
| N2 | NIT   | `__main__.py`                        | Add `# pragma: no cover` to `if __name__ == "__main__":` line (covered by subprocess test, not by coverage). |

### 1.2 Findings explicitly DEFERRED (documentation only this pass)

| #  | Sev   | One-line reason for deferral                                                                                          | Doc update applied this pass |
|----|-------|-----------------------------------------------------------------------------------------------------------------------|------------------------------|
| M4 | MED   | Streaming event-load — current `evaluate_case` reads whole fixture into memory; <1 MB cap (H2) keeps RAM bounded.       | Inline `# TODO(step-10-hardening)` marker in `evaluate_case` |
| L2 | LOW   | Implicit greedy-on-`case.expected` match order; structural determinism is fixed by H1 anomaly sort.                    | Docstring line on `_match_expecteds_to_anomalies` |

See §6 for the full deferral table.

---

## 2. Phased Fix Sequence

Edits are grouped by risk and applied in this exact order so each phase is
small, reviewable, and verifiable before the next begins. No phase touches
any build files outside the enumerated plan + lessons targets.

### Phase 1 — Zero-risk polish (no behavior change)

Targets: **N1, N2, L2 (docstring), M4 (TODO comment)**.

- **N1** — `evaluation.py` imports: scan for `from dataclasses import dataclass, ...`
  style namespace imports. If any imported name is unused, drop it. Keep
  only the names referenced in the module body. Verify with `ruff F401`.
- **N2** — `src/opsmitra/__main__.py`: append `# pragma: no cover` to the
  `if __name__ == "__main__":` line. Rationale: the line is exercised only
  by a subprocess-invoked test; the in-process coverage instrument cannot
  see it, so the line shows red despite being covered. Pragma reflects
  reality.
- **L2 (defer with docstring)** — `evaluation.py`: locate the bipartite
  match helper (the function that walks `case.expected` against
  `anomalies`; per review at `evaluation.py:~302`). Add a one-line
  docstring entry:
  `"Greedy in expected iteration order; first-matched expected consumes the anomaly. Anomalies are pre-sorted (see H1) for determinism."`
- **M4 (defer with TODO)** — `evaluation.py:evaluate_case`: add a single
  inline comment just above the events-file read (currently a full-file
  load):
  `# TODO(step-10-hardening): switch to streaming if fixture sizes grow above 1 MB cap.`
  This is a grep-friendly marker that mirrors the style of the Step 8
  `# TODO(step-10-hardening): pass DetectorThresholds → DetectionConfig`
  comment.

**Verifier**: `ruff check`, `black --check`, full suite green — zero
behavior change expected; all import-removal failures surface immediately
as `NameError`.

### Phase 2 — Field rename (M2, behavior-preserving)

Targets: **M2**.

- **M2** — Rename `detection_delays_seconds` →
  `detection_delay_upper_bound_seconds` across:
  - `evaluation.py`:
    - `CaseResult.detection_delays_seconds` → `detection_delay_upper_bound_seconds`
    - `EvaluationReport.delays_summary` — **keep name** (already aggregated,
      not the upper-bound semantic).
    - Any JSON-output construction that emits the key (search for the
      literal string `detection_delays_seconds`).
    - `format_report_table` columns: header `p50` → `p50_ub`, `p95` →
      `p95_ub`.
  - All tests asserting on `detection_delays_seconds` or the table
    headers `p50` / `p95` — update assertion strings to the new names.
- Rationale: the formula `window_end - first_seen` is the **upper bound**
  of detection delay (anomaly may have been detectable mid-window). The
  name should not promise a tighter measurement than the computation
  delivers. This is a doc/honesty fix, not a math fix.

**Verifier**: full suite green after the rename; `grep -r
detection_delays_seconds src/ tests/` returns empty (allowing the
preserved `delays_summary` aggregate name); `pytest -q` green.

### Phase 3 — Statistics correctness (M3 + L1)

Targets: **M3, L1**.

- **M3** — `evaluation.py:_summarize_delays`: switch the p95 computation:
  - When `len(data) >= 100`, use `statistics.quantiles(data, n=100,
    method='inclusive')[94]`.
  - When `len(data) < 100`, retain an empirical nearest-rank fallback
    (`data[int(0.95 * (n-1))]` after sorting) and document the choice in
    a docstring comment. Rationale: `quantiles` with `n=100` requires at
    least 2 samples but is unstable for small N; the empirical formula
    is interpolation-free and acceptable for evaluation fixtures.
  - Same branching for p50 (use `statistics.median` for all N — already
    behaves correctly).
- **L1** — `evaluation.py:_normalize_subject_dict`: add a defensive check
  before normalizing each value:
  ```
  if not isinstance(value, (str, int, float, bool, type(None))):
      raise TypeError(
          "subject values must be JSON-serializable primitives; got %s"
          % type(value).__name__
      )
  ```
  Today the function silently coerces (or worse, fails opaquely) on
  list/dict/datetime values. Failing loudly at the boundary mirrors the
  validation discipline elsewhere in `config.py`.

**Verifier**: two new tests added (see §3); existing
`_summarize_delays` tests continue to pass (the small-N empirical
fallback preserves their expected values).

### Phase 4 — Fixture size cap (H2)

Targets: **H2**.

- **H2** — `evaluation.py`:
  - Add module-level constant: `_MAX_EVENTS_FILE_BYTES = 1_000_000`
    (mirrors `_MAX_THRESHOLDS_FILE_BYTES` in `config.py`).
  - In `load_case`, before opening `events_path` for read, call
    `events_path.stat()` and check `.st_size`. If size > cap, raise:
    `ValueError("evaluation events file %s exceeds %d bytes" % (events_path, _MAX_EVENTS_FILE_BYTES))`.
  - The cap is **1 MB**, not 100 KB. Rationale: committed fixtures are
    580–790 KB (verified `ls -la tests/fixtures/evaluation/*.events.jsonl`);
    raising the cap to 1 MB matches the cooldown / thresholds precedent
    in `config.py` and gives ~25% headroom over existing fixtures.
- New test (see §3): `test_load_case_rejects_oversized_events_file`.
- Plan doc update (Phase 6): `step9-evaluation-harness.md` §3 and §8
  wording: 100 KB → 1 MB.

**Verifier**: new test passes; existing fixture-loading tests pass (all
existing fixtures are well under 1 MB). `grep -r "100 KB"
.claude/plans/step9-evaluation-harness.md` returns zero hits after the
plan doc update.

### Phase 5 — FP correctness (H1)

Targets: **H1**.

- **H1** — `evaluation.py:~302`: before the greedy bipartite match loop,
  sort the `anomalies` list deterministically:
  ```
  sorted(
      anomalies,
      key=lambda a: (
          a.type,
          a.tenant_id or "",
          json.dumps(a.subject, sort_keys=True, default=str),
      ),
  )
  ```
  Without this sort, the FP count depends on dict insertion order, which
  is stable within a Python run but not across runs that produce
  anomalies in different orders (e.g. parallel detection in Step 10).
- Update the `evaluate_case` docstring to document the determinism
  contract:
  `"Anomalies are pre-sorted by (type, tenant_id, subject-json) before bipartite match. Greedy in expected iteration order: first-matched expected consumes the anomaly."`
- New test (see §3):
  `test_evaluate_case_overlap_matches_deterministic` — two expected
  records that could each match either of two anomalies; assert the FP
  count is 0 and the match assignment is stable across two `evaluate_case`
  invocations of the same case.

**Verifier**: new test passes; existing `evaluate_case` tests pass (the
sort is a no-op when only one anomaly matches each expected, which is
the common case in current fixtures).

### Phase 6 — Strict mode exit-code semantics (H3)

Targets: **H3**.

- **H3** — `cli.py:_cmd_eval` exit-code logic: replace the current
  branch with:
  ```
  critical_missed = sum(len(c.missed_critical) for c in report.cases)
  any_missed = report.totals.get("missed", 0) > 0
  if args.strict:
      return 1 if critical_missed > 0 else 0
  return 2 if any_missed else 0
  ```
- Update CLI `--strict` help text to:
  `"Exit 1 if any must_detect incident is missed (ignores non-critical misses)"`.
- New test (see §3):
  `test_cli_eval_strict_non_critical_miss_exits_zero` — fixture with a
  non-critical miss; run under `--strict`; expect exit 0.
- Verify existing `test_cli_eval_strict_missed_critical_exits_one`
  still passes (semantics unchanged for the critical-miss case).
- Plan doc update (Phase 7): `step9-evaluation-harness.md` §9 wording —
  clarify strict mode applies only to `must_detect` misses.

**Verifier**: new test passes; existing strict-mode tests pass without
modification.

### Phase 7 — Remove dead parameter (M1) + plan-doc updates

Targets: **M1**, plus consolidated plan-doc edits from Phases 2/4/6.

- **M1** — Remove `runtime_factory` parameter from public signatures:
  - `evaluate_case(case, *, runtime_factory=None, ...)` →
    `evaluate_case(case, ...)`.
  - `evaluate_all(cases, *, runtime_factory=None, ...)` →
    `evaluate_all(cases, ...)`.
  - Grep `src/` and `tests/` for `runtime_factory=` — expected: zero
    call sites (plan ignored the parameter throughout). If a test does
    pass it, drop the kwarg from that test.
- Plan-doc updates (apply now, batched into one edit pass on
  `step9-evaluation-harness.md`):
  - §3: fixture size cap "100 KB" → "1 MB".
  - §4: rename metric `detection_delays_seconds` →
    `detection_delay_upper_bound_seconds`. Update column headers
    `p50` → `p50_ub`, `p95` → `p95_ub` wherever §4 shows the table.
  - §5: remove `runtime_factory` parameter from `evaluate_case` and
    `evaluate_all` signatures.
  - §8: same 100 KB → 1 MB rewording.
  - §9: clarify strict mode applies only to `must_detect` misses;
    non-strict soft-fails (exit 2) on any miss.
  - §10: remove `runtime_factory` reference.

**Verifier**: `grep -r runtime_factory src/ tests/` returns zero hits
after the source rename; `grep -r runtime_factory
.claude/plans/step9-evaluation-harness.md` returns zero hits after the
plan update.

### Phase 8 — Lessons.md additions

Targets: **lessons.md** additions (load-bearing for justifying M1 / M4
deferral and the M2 / H2 design choices).

Append three lessons to `.claude/tasks/lessons.md` under "Coding
Lessons" (or the project's equivalent header — match existing format):

1. **Documented-but-dead parameters violate YAGNI.** If a parameter is
   documented as "accepted for API compatibility" but never used,
   remove it. Future readers will assume it does something. *(Justifies
   M1 removal.)*
2. **Metric names must be semantically honest.**
   `detection_delay_upper_bound_seconds` is harder to type than
   `detection_delays_seconds`, but it tells the on-call engineer what
   the number means. Conservative metrics with optimistic names hide
   bugs. *(Justifies M2 rename.)*
3. **Mirror runtime-config size-budget discipline in fixture files.**
   When committing fixture files for CI, use the same size-budget
   discipline as runtime config files. 1 MB cap with a field-named
   `ValueError` matches the cooldown / thresholds precedent.
   *(Justifies H2 cap + error shape.)*

**Verifier**: `grep "Documented-but-dead" .claude/tasks/lessons.md` returns
exactly one line after the edit; same for the two other lesson titles.

---

## 3. New & Updated Tests — Consolidated List

Five strictly required new tests; all other fixes preserve existing
test coverage (refactors, comments, docstrings, header renames, removed
parameters) or update test bodies in-place.

### 3.1 `tests/test_evaluation.py` — +4 tests

1. **`test_evaluate_case_overlap_matches_deterministic` (H1)** —
   - Build a case with 2 expecteds and 2 anomalies where either
     expected could match either anomaly under a naive match.
   - Run `evaluate_case` twice; assert identical `false_positives` count
     (0) and stable `detected` count across the two invocations.
   - Assert FP count is correct (0) when each expected gets its own
     anomaly.
2. **`test_load_case_rejects_oversized_events_file` (H2)** —
   - Use `tmp_path` to write a `>1_000_000`-byte events file (e.g. a
     1.5 MB stub of dummy event lines).
   - Write a matching minimal `.expected.json`.
   - Call `load_case(tmp_path)` and expect `ValueError` whose message
     contains `"exceeds"` and the byte count.
3. **`test_p95_uses_nearest_rank_for_large_samples` (M3)** —
   - Construct a list of 100 values `[0, 1, 2, ..., 99]`.
   - Call `_summarize_delays(values)`.
   - Assert `p95_ub` equals the documented nearest-rank value (94 or 95
     depending on which method choice the fix lands on; pin it in the
     test). For `statistics.quantiles(..., n=100, method='inclusive')`
     on `range(100)`, expect 94.05 → assert `≈ 94` with tolerance.
4. **`test_normalize_subject_rejects_non_primitive_values` (L1)** —
   - Call `_normalize_subject_dict({"key": [1, 2, 3]})` (or `{"key":
     datetime.now()}`).
   - Expect `TypeError` whose message contains `"primitive"` and
     `"list"` (or the appropriate type name).

### 3.2 `tests/test_cli.py` — +1 test

5. **`test_cli_eval_strict_non_critical_miss_exits_zero` (H3)** —
   - Use a fixture (or `tmp_path` + a minimal `.events.jsonl` /
     `.expected.json` pair) where the missed expected has
     `must_detect: false`.
   - Invoke the CLI as `["opsmitra", "eval", "--dataset", <tmp>,
     "--strict"]`.
   - Assert exit code 0.
   - Companion: re-verify
     `test_cli_eval_strict_missed_critical_exits_one` still asserts
     exit code 1 (semantics unchanged for critical misses).

### 3.3 Updated tests (in-place body changes only)

- **M2 rename** — any test asserting on the literal
  `detection_delays_seconds` field name or on the table headers `p50`
  / `p95` updates to the new names (`detection_delay_upper_bound_seconds`
  /  `p50_ub` / `p95_ub`). Expected reach:
  `tests/test_evaluation.py`, `tests/test_cli.py` (table-format test).
  Grep first; touch only the assertions that fail.
- **M1 removal** — grep tests for `runtime_factory=` and drop the
  kwarg. Expected: zero hits.

### 3.4 No new tests required for

- **N1** (unused import — covered by `ruff F401`), **N2** (pragma — no
  observable behavior), **L2** (docstring only — structural cause
  covered by H1's new test), **M4** (TODO comment — no behavior),
  **M1** (parameter removal — observable as absence in signature;
  covered by `mypy` if run + existing call sites).

### 3.5 Optional / nice-to-have (not required by acceptance)

- A negative test for the small-N branch of M3
  (`test_p95_small_sample_uses_empirical_formula`) — not required;
  existing summarizer tests already exercise small-N values.

---

## 4. Acceptance

After the FIX phase runs and RE-VERIFY completes:

1. **All 3 Step 9 acceptance criteria still PASS** (re-verified verbatim
   per `.claude/plans/step9-evaluation-harness.md` §1):
   - AC1 — `opsmitra eval` runs against fixture datasets; exit code 0 on
     clean fixtures, exit 2 on misses, exit 1 on `--strict` + critical
     miss (H3 refines this).
   - AC2 — Report still includes `detected`, `missed`,
     `false_positives`, and (renamed) `detection_delay_upper_bound_seconds`.
   - AC3 — Tests still fail when known critical incidents are missed
     (H3 is a refinement of strict-mode semantics, not a regression).
2. **Test suite green**:
   - Existing **224 tests** + **5 new** = **≥ 229 passing**, zero
     removed.
3. **Coverage floors** (matching or exceeding Step 9 baseline):
   - `src/opsmitra/evaluation.py` ≥ **95%** (was 97%; small dip
     allowed for new branches in `_summarize_delays`, `load_case`,
     `_normalize_subject_dict`).
   - `src/opsmitra/cli.py` ≥ **86%**.
   - Suite total ≥ **95%**.
4. **No regression on Steps 1–8** — all baseline tests from prior steps
   remain green.
5. **`ruff` + `black` clean** on `src/` and `tests/`. `ruff F401`
   reports zero unused imports.

---

## 5. Risks

| Phase | Risk | Mitigation |
|------|------|------------|
| 1 | N1 import removal hides a transitive re-export some external caller relies on. | All removed imports are confined to `src/opsmitra/evaluation.py`. No public API change. `ruff F401` catches transitive use. |
| 1 | N2 pragma misapplies if the line is later moved or split. | Pragma is on the exact `if __name__ == "__main__":` line; future authors can re-evaluate when the file changes. |
| 2 | M2 rename breaks a downstream consumer that greps for the old field name. | OpsMitra v0 has no documented external eval-output contract. Internal callers all live in this repo and update with the rename. |
| 3 | M3 `statistics.quantiles` rejects N < 2; the branch must skip it. | The new code branches on `len(data) >= 100`; for small N the empirical fallback applies. |
| 3 | L1 strict `TypeError` breaks a fixture that legitimately uses nested values. | Existing fixtures (verified via Step 9 review summary) use only flat primitives. The strict guard tightens the implicit contract that already existed. |
| 4 | H2 cap rejects an existing fixture. | Existing fixtures are 580–790 KB, well under 1 MB. Re-verify with `ls -la tests/fixtures/evaluation/*.events.jsonl` before edit. |
| 5 | H1 sort changes the match assignment for an existing case in a way that flips a current pass to a fail. | The sort is deterministic but may differ from current insertion order. Pre-edit, run the existing eval suite; if any case's `detected` / `missed` counts change, investigate before locking in. The expected behavior is that match counts stay the same (they're independent of order under a valid bipartite match) — only the *which-anomaly-matches-which-expected* assignment changes. |
| 6 | H3 changes the exit code of a CI workflow that currently relies on exit 1 for non-critical misses under strict. | No external CI workflow is documented; the change aligns implementation with plan §9. The new `test_cli_eval_strict_non_critical_miss_exits_zero` is the authoritative coverage of the new semantic. |
| 7 | M1 removal breaks a test that hand-rolls `runtime_factory=`. | Grep expected to return zero; pre-edit grep is mandatory. |
| 8 | Lessons.md edit conflicts with concurrent edits on `dev`. | Apply during Phase 8 after all source edits land; one-shot append. |

---

## 6. Deferred Items (Step 10 Hardening candidates)

| ID  | Item                                                              | Why deferred                                                                                                  | Target |
|-----|-------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|--------|
| M4  | Streaming event-load for `evaluate_case`                          | <1 MB cap (H2) keeps RAM bounded; in-memory load is OK for v0 fixtures. TODO marker added this pass.            | Step 10 |
| L2  | Implicit greedy-on-`case.expected` match order                    | Structural determinism is fixed by H1 anomaly sort; docstring on the match helper is sufficient for v0.        | (closed via docstring) |

**Note**: M4 receives an inline `# TODO(step-10-hardening)` comment
*this pass* (Phase 1). L2 is closed via a one-line docstring *this pass*
(Phase 1) — no Step 10 follow-up needed since H1 closes the structural
cause.

---

## 7. Lessons to Capture (during this fix pass — Phase 8)

Three lessons are added to `.claude/tasks/lessons.md` as part of Phase
8. These are **load-bearing for the fix pass**, not deferred to Step 9
update-docs, because they directly justify the M1 removal and the M2 /
H2 design choices.

> **Documented-but-dead parameters violate YAGNI.** If a parameter is
> documented as "accepted for API compatibility" but never used, REMOVE
> it. Future readers will assume it does something. *(Justifies M1
> removal.)*

> **Metric names must be semantically honest.**
> `detection_delay_upper_bound_seconds` is harder to type than
> `detection_delays_seconds`, but it tells the on-call engineer what
> the number means. Conservative metrics with optimistic names hide
> bugs. *(Justifies M2 rename.)*

> **Mirror runtime-config size-budget discipline in fixture files.**
> When committing fixture files for CI, mirror the same size-budget
> discipline as runtime config files. 1 MB cap with a field-named
> `ValueError` matches the cooldown / thresholds precedent. *(Justifies
> H2 cap + error shape.)*

Any other lessons surfaced during the Step 9 workflow (e.g. from the
COMPLETE phase) go into `lessons.md` during the update-docs step, not
this fix pass.

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
   - `src/opsmitra/evaluation.py`, `cli.py`, `__main__.py`
   - `tests/test_evaluation.py`, `tests/test_cli.py`
   - `tests/fixtures/evaluation/` (verify all `*.events.jsonl` are < 1 MB
     via `ls -la`)
   - `.claude/plans/step9-evaluation-harness.md`
   - `.claude/tasks/lessons.md`
3. Pre-edit greps:
   - `grep -rn "runtime_factory" src/ tests/` (expect zero call sites;
     possibly one definition + docstring in `evaluation.py`).
   - `grep -rn "detection_delays_seconds" src/ tests/` (capture the full
     hit set so Phase 2 rename covers all of them).
   - `grep -rn "100 KB" .claude/plans/step9-evaluation-harness.md`
     (capture for Phase 7 plan update).
   - `ls -la tests/fixtures/evaluation/*.events.jsonl` (confirm all
     under 1 MB; reject the fix plan and escalate if any exceed).

### 8.2 Edit order (8 phases)

Apply in this exact order. After each phase, run §8.3 quick check.

1. **Phase 1** — N1 (imports), N2 (pragma), L2 (docstring), M4 (TODO).
2. **Phase 2** — M2 (field rename + table headers).
3. **Phase 3** — M3 (p95 nearest-rank) + L1 (subject-value guard) + 2
   new tests.
4. **Phase 4** — H2 (`_MAX_EVENTS_FILE_BYTES` + `load_case` stat-check)
   + 1 new test.
5. **Phase 5** — H1 (anomaly sort + docstring) + 1 new test.
6. **Phase 6** — H3 (`_cmd_eval` exit-code rewrite + `--strict` help
   text) + 1 new test.
7. **Phase 7** — M1 (drop `runtime_factory` parameter) + plan-doc
   updates batched (§3, §4, §5, §8, §9, §10 of
   `step9-evaluation-harness.md`).
8. **Phase 8** — lessons.md three appends.

### 8.3 Per-phase quick check

```
cd /Users/bhushan/Coding/Projects/OpsMitra
ruff check src/ tests/
pytest -q
```

If either fails, stop and report the failing output. Do **not** "fix it
harder" by adding new code paths.

### 8.4 Final post-flight

```
cd /Users/bhushan/Coding/Projects/OpsMitra
ruff check src/ tests/
black --check src/ tests/
pytest -q --cov=src --cov-report=term-missing
```

Required outcomes:
- `ruff check`: no errors, zero F401.
- `black --check`: clean.
- `pytest`: ≥ 229 passing (224 baseline + 5 new).
- `evaluation.py` ≥ 95%, `cli.py` ≥ 86%, suite ≥ 95%.

### 8.5 Stop conditions

After successful post-flight, report:
- The list of files edited per phase.
- The `pytest` summary line.
- The coverage % for `evaluation.py`, `cli.py`, and the suite total.
- Do **NOT** commit. Do **NOT** push. Do **NOT** advance to RE-VERIFY.
  The main session will run RE-VERIFY via the `verify` skill next.

---

## 9. Out of Scope for This Fix Pass

- The 2 deferred review findings as code changes (see §1.2 and §6); only
  documentation/comment markers are applied this pass.
- Any new features, refactors, or doc updates beyond what is enumerated
  above (e.g. no fixture regeneration, no new evaluation metrics, no
  parallel-eval support).
- Coverage uplift beyond the floors in §4.
- Commit / push / PR — those happen only after RE-VERIFY and user
  confirmation.

---

## 10. Ready-for-FIX Checklist

- [x] All 9 applied findings have a precise file:line target.
- [x] Each finding is grouped into one of 8 phases.
- [x] New tests enumerated (5 required: H1, H2, H3, M3, L1).
- [x] Deferred items listed with rationale and doc-update plan (2 items,
      §6).
- [x] Acceptance bar specifies test count, coverage floors, and AC re-check.
- [x] Risks enumerated per phase.
- [x] FIX-agent instructions are sequenced, phase-bounded, and unambiguous.
- [x] Three lessons captured *in this fix pass* (Phase 8); broader lessons
      deferred to Step 9 update-docs.
- [x] Plan-doc updates to `step9-evaluation-harness.md` enumerated (§3,
      §4, §5, §8, §9, §10).
- [ ] **User confirmation** to proceed to FIX phase (sonnet agent) —
      blanket approval granted; no wait required.

**Blanket approval acknowledged**: proceed to Step 9 FIX with this plan.
