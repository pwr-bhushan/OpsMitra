# Step 8: Fix Plan — Address Step 8 Review Findings

**Step 8 Plan reference**: [`.claude/plans/step8-scheduler-runtime.md`](./step8-scheduler-runtime.md)
**Review verdict from Step 8**: **APPROVE WITH MINORS** (no CRITICAL)
**Branch**: `dev` (mandatory per `.claude/tasks/lessons.md`)
**Workflow phase**: FIX PLAN (no code in this phase)
**Severity counts**: 2 HIGH, 5 MEDIUM, 5 LOW, 4 NIT, 0 CRITICAL

---

## 1. Scope of This Fix Pass

The Step 8 `python-review` returned **APPROVE WITH MINORS**. Of the 16 findings
raised, this plan **applies 12** and **defers 4** with documentation-only
follow-ups. No CRITICAL exists.

- **2 HIGH** — H1, H2 (both applied — entry point + send-mode guard)
- **3 MEDIUM** — M3, M4, M5 (applied); **M1, M2 deferred with doc updates**
- **4 LOW** — L1, L2, L4, L5 (applied); **L3 deferred with docstring note**
- **3 NIT** — N1, N2, N3 (applied); **N4 deferred with comment**

### 1.1 Findings addressed in this fix pass

| #  | Sev   | File:Line                          | One-line description                                                                                |
|----|-------|------------------------------------|------------------------------------------------------------------------------------------------------|
| H1 | HIGH  | `src/opsmitra/__main__.py` (new)   | Add 2-line file so `python -m opsmitra` works.                                                       |
| H2 | HIGH  | `runtime.py:Runtime.__init__`      | Raise `RuntimeConfigurationError` if `dry_run=False` and `alerter.config.slack_webhook_url is None`. |
| M3 | MED   | `runtime.py:217`                   | Add inline `# TODO(step-10-hardening): pass DetectorThresholds → DetectionConfig` marker.            |
| M4 | MED   | `runtime.py:73`                    | Change `errors: list[str]` → `errors: tuple[str, ...]` on frozen `RuntimeResult`; convert at build.  |
| M5 | MED   | `runtime.py:_safe_error_message`   | Drop `repr()` wrapper around `type(exc).__name__` in fallback path.                                  |
| L1 | LOW   | `runtime.py:249`                   | Fold direct `logger.warning(...)` into `_log_event(WARNING, "summarizer_failed", ...)`.              |
| L2 | LOW   | `runtime.py:309`                   | Rename `errors=...` → `error_count=...` in `runtime_done` log (consistency with `_print_result`).    |
| L4 | LOW   | multiple                           | Remove unused imports across `runtime.py`, `cli.py`, `tests/test_runtime.py`, `tests/test_cli.py`.   |
| L5 | LOW   | `runtime.py:Runtime.__init__`      | Replace `Any` with `_SummarizerProto` / `_AlerterProto`; keep `EventSource` for `source`.            |
| N1 | NIT   | `cli.py:main`/`_compose_and_execute` | Parse `--window-start` / `--window-end` once in `main()`, stash on args, reuse downstream.        |
| N2 | NIT   | `cooldown.py:AnomalyCooldown`      | `should_alert` / `record_alerted` / `prune_expired`: raise `ValueError` if `now.tzinfo is None`.    |
| N3 | NIT   | `cooldown.py:AnomalyCooldown.load` | Add 1-line idempotency docstring: "Safe to call multiple times; state is replaced, not merged."     |

### 1.2 Findings explicitly DEFERRED (documentation only this pass)

| #  | Sev   | One-line reason for deferral                                                                                                              | Doc update applied this pass |
|----|-------|-------------------------------------------------------------------------------------------------------------------------------------------|------------------------------|
| M1 | MED   | `OPSMITRA_LOG_PATH` vs `OPSMITRA_LOCAL_EVENTS_PATH` doc-vs-code drift; rename in code would cascade through tests + lessons.               | Update Step 7 plan + lessons.md to match code |
| M2 | MED   | Exit code 2 ambiguity (argparse misuse vs anomalies detected); full remap to EX_USAGE (64) would touch every CLI test + need argparse subclass. | `--help` text update only |
| L3 | LOW   | Concurrent-writer footgun on cooldown JSON; docstring warning sufficient for single-process v0.                                            | `AnomalyCooldown` class docstring warning |
| N4 | NIT   | `fsync` before `os.replace` for cooldown durability across host crash; cooldown is performance, not correctness.                           | One-line comment near temp-file close |

See §6 for the full deferral table.

---

## 2. Phased Fix Sequence

Edits are grouped by risk and applied in this exact order so each phase is
small, reviewable, and verifiable before the next begins. No phase touches
any plan/build files (per main-session instruction); fix-agent will edit
only source, tests, lessons.md, and the Step 7 plan env-var section.

### Phase 1 — Zero-risk polish (no behavior change)

Targets: **L4, L2, N3, N4 (comment), L3 (docstring), M3 (TODO marker)**.

- **L4** — Remove unused imports:
  - `runtime.py:23-25` — drop `DetectionConfig`, `Event`, `AlertSummary`
    (verify each is truly unreferenced; preserve any docstring references via
    `# noqa: F401` only if grep confirms use).
  - `cli.py:109` — drop unused `load_thresholds` top-level import (the alias
    `_load_thresholds` is imported again at line 162; reconcile to one
    import).
  - `cli.py:165` — drop shadowing `import sys` (module-level `import sys`
    at line 7 already exists; the inner re-import is dead).
  - `tests/test_runtime.py:19` — drop unused `MagicMock`.
  - `tests/test_cli.py:10-12, :14` — drop unused `io`, `json`, `sys`,
    `MagicMock`.
- **L2** — `runtime.py:309`: rename the `errors=len(errors)` kwarg in the
  `runtime_done` `_log_event(...)` call to `error_count=len(errors)`. This
  matches the `_print_result` summary key (`"error_count": ...`) so a log
  consumer + JSON-summary consumer see the same field name.
- **N3** — `cooldown.py:AnomalyCooldown.load`: append one line to the
  existing docstring: `"Idempotent: safe to call multiple times; state is
  replaced, not merged."`
- **N4 (defer w/ comment)** — `cooldown.py:persist`: add a comment near the
  temp-file close (before `os.replace`) reading:
  `# Cooldown is performance, not correctness; cross-crash durability
  (fsync) deferred to Step 10.`
- **L3 (defer w/ docstring)** — `cooldown.py:AnomalyCooldown` class
  docstring: append one line: `"WARNING: single-process backend;
  concurrent Runtimes will race on persist (Step 10 will swap for
  DynamoDB/fcntl)."`
- **M3 (inline TODO marker only)** — `runtime.py` at the
  `detect_anomalies(raw_events, window_start, window_end, config=None)` call
  site (currently around line 216-218): add a single-line inline comment
  above the call:
  `# TODO(step-10-hardening): pass DetectorThresholds → DetectionConfig`
  This is a grep-friendly marker that mirrors the style at `config.py:46`.
  Existing docstring at `runtime.py:106-107` already acknowledges this;
  the inline marker makes it discoverable from the call site.

**Verifier**: `ruff check`, `black --check`, full suite green — zero behavior
change expected; all import-removal failures surface immediately as
`NameError`.

### Phase 2 — Small structural (behavior-preserving)

Targets: **M5, L1, N1, N2, M4**.

- **M5** — `runtime.py:_safe_error_message`: change `return
  repr(type(exc).__name__)` → `return type(exc).__name__`.
  Today this produces `"'RuntimeError'"` (with quotes); after the fix it
  produces `"RuntimeError"` (no quotes). Update any test that asserts on
  the quoted form. Grep `tests/` for `"'RuntimeError'"` style assertions
  before edit.
- **L1** — `runtime.py:249`: replace
  ```python
  logger.warning(
      "event=summarizer_failed fingerprint=%s exc_type=%s; using fallback",
      fp,
      type(exc).__name__,
  )
  ```
  with
  ```python
  _log_event(
      logging.WARNING,
      "summarizer_failed",
      fingerprint=fp,
      exc_type=type(exc).__name__,
  )
  ```
  All other stage logs in `Runtime.execute` route through `_log_event`; this
  was the lone bypass. Trailing `"; using fallback"` becomes implicit from
  the event_type name.
- **N1** — `cli.py:main`: parse the ISO timestamps **once** before
  `_compose_and_execute`:
  - Currently `_parse_iso_timestamp` is called twice for each window arg
    (once in `main()` for friendly error, once in `_compose_and_execute`
    via `runtime.execute`).
  - After the existing pre-flight `_parse_iso_timestamp(args.window_start, …)`
    calls (lines 245, 250), stash the parsed `datetime` on the namespace:
    `args.window_start_dt = ...`, `args.window_end_dt = ...`.
  - In `_compose_and_execute`, replace the lines 188-189
    `window_start = _parse_iso_timestamp(args.window_start, "window-start")`
    with `window_start = args.window_start_dt` (same for `window_end`).
  - Net effect: one parse per arg per invocation; behavior identical.
- **N2** — `cooldown.py`: add the same defensive tz-aware check to all three
  public methods accepting a `now: datetime | None = None`:
  ```python
  if now is not None and now.tzinfo is None:
      raise ValueError("now must be timezone-aware")
  ```
  Apply to `should_alert`, `record_alerted`, `prune_expired`. Place the
  check immediately after the parameter is consulted, before `current_time
  = now or self._now()`.
- **M4** — `runtime.py:RuntimeResult`: change field declaration from
  `errors: list[str] = field(default_factory=list)` to
  `errors: tuple[str, ...] = ()`.
  - In `Runtime.execute`, keep the local `errors: list[str] = []` for
    append-friendly accumulation.
  - At the `RuntimeResult(...)` construction site, pass
    `errors=tuple(errors)`.
  - Update any test that did `result.errors.append(...)` or
    `result.errors[0]` — the latter still works on tuples; only mutation
    sites need update (likely zero in current tests).
  - Update `len(result.errors)` and any iteration — both work unchanged.

**Verifier**: new test (single — see §3) for M5 string shape;
existing tests stay green after L4 import cleanup and L2 log-field rename.
The `runtime_done` log field name change (L2) may surface in
`tests/test_runtime_logging.py` if it asserts on `errors=` literal; verify
and adjust assertion if needed (likely already checks for `event=runtime_done`
only).

### Phase 3 — CLI entry point + help-text update

Targets: **H1, M2 (help-text only)**.

- **H1** — Create `src/opsmitra/__main__.py` (new file, 2 lines):
  ```python
  from opsmitra.cli import main
  import sys
  sys.exit(main())
  ```
  This makes `python -m opsmitra` work. Add a single test in
  `tests/test_cli.py`:
  - `test_python_dash_m_entry_point_invokes_main` — use
    `runpy.run_module("opsmitra", run_name="__main__")` with a stubbed
    `sys.argv = ["opsmitra", "--version"]` and assert `SystemExit` with
    code 0. (Or simpler: `subprocess.run([sys.executable, "-m", "opsmitra",
    "--version"], capture_output=True)` and assert `returncode == 0` and
    version string in stdout.)
- **M2 (defer with documentation)** — In `cli.py:build_parser`, change the
  `description=` argument on the main `ArgumentParser` to append a one-line
  note:
  ```
  description=(
      "Private AI-assisted log anomaly detector.\n"
      "Exit codes: 0 = clean; 1 = runtime/config error; "
      "2 = anomalies detected OR argparse parse error (check stderr to disambiguate)."
  )
  ```
  Use `formatter_class=argparse.RawDescriptionHelpFormatter` if needed to
  preserve newlines. No semantic change; documentation only. Full EX_USAGE
  (64) remap is deferred to Step 10 (see §6).

**Verifier**: `python -m opsmitra --version` exits 0. New `__main__.py` test
passes. Help-text test (if any exists) updated for the new description; or
add a small `test_help_documents_exit_codes` smoke test asserting `"Exit
codes"` appears in `parser.format_help()`.

### Phase 4 — Runtime guard for send-without-webhook

Targets: **H2**.

- **H2** — `runtime.py:Runtime.__init__`: after assigning `self._alerter =
  alerter` and `self._dry_run = dry_run`, add a guard:
  ```python
  if not self._dry_run and self._alerter.config.slack_webhook_url is None:
      raise RuntimeConfigurationError(
          "Slack webhook URL required when dry_run is false"
      )
  ```
  - The guard lives at the library boundary (Runtime), not the CLI, so
    library consumers also get the safety.
  - Message names the field semantics ("Slack webhook URL", "dry_run") but
    **must not** include the URL value itself or any environment variable
    name that could mislead about where to set it.
  - Today the failure mode is per-anomaly redacted exceptions during
    `_post_with_retry`; this raises one clear error at construction time
    instead.
- New test in `tests/test_runtime.py`:
  - `test_runtime_rejects_send_mode_without_webhook` — construct an
    `AlertConfig` with `slack_webhook_url=None, dry_run=False`, build a
    `SlackAlerter`, then call `Runtime(...)` with `dry_run=False`. Expect
    `RuntimeConfigurationError`. Assert the error message contains
    `"dry_run"` and **does not** contain `"https://"`, `"hooks.slack.com"`,
    or the literal `"webhook URL"` substring leaking a hint at a URL
    value. (The phrase "Slack webhook URL" is acceptable; the prohibition
    is on URL-shaped substrings.)
  - **Refine guidance**: the assertion is exact-string: `"https://" not in
    str(exc)` and `"hooks.slack.com" not in str(exc)`. The phrase "webhook
    URL required" is the intended message and should appear.
- Plan doc update: also update `step8-scheduler-runtime.md` §6 Stage 1
  (line 199) to reflect that the `--send` with no webhook guard now lives
  in `Runtime.__init__`, not CLI/Stage 1 (the plan currently says "Inspect
  injected config; raise RuntimeConfigurationError" — keep the description
  but add "(now at constructor time)" so the plan reflects implementation
  truth).

**Verifier**: new test passes; existing `test_runtime.py` tests that build
`Runtime` in `dry_run=True` mode (the default) continue to pass without
change. Any existing `dry_run=False` test must either pass a valid webhook
URL or be updated.

### Phase 5 — Typed collaborator Protocols

Targets: **L5**.

- **L5** — `runtime.py`: replace `Any` typing for `summarizer` and
  `alerter` parameters with structural Protocols defined inside
  `runtime.py`:
  ```python
  from typing import Protocol, runtime_checkable
  from opsmitra.event_source import EventSource
  from opsmitra.summarizer import AlertSummary

  @runtime_checkable
  class _SummarizerProto(Protocol):
      def summarize(self, anomaly: Anomaly) -> AlertSummary: ...

  @runtime_checkable
  class _AlerterProto(Protocol):
      def send(self, summary: AlertSummary, anomaly: Anomaly) -> Any: ...
  ```
  Then `Runtime.__init__` signature becomes:
  ```python
  def __init__(
      self,
      source: EventSource,
      summarizer: _SummarizerProto,
      alerter: _AlerterProto,
      cooldown: AnomalyCooldown,
      thresholds: DetectorThresholds,
      ...
  )
  ```
- Notes:
  - `EventSource` is already a Protocol exported from `event_source.py`;
    re-use it as-is (do not duplicate).
  - The `_AlerterProto.send` return type is `Any` not `DeliveryResult` to
    avoid cross-module import of the `DeliveryResult` dataclass from
    `slack_alerter.py` (which is a deeper module than runtime should pull
    in). The `.delivered`, `.dry_run`, `.attempts` accesses are duck-typed
    at the call site.
  - The `@runtime_checkable` decorator means tests can still pass
    `Mock(spec=SlackAlerter)` without explicit subclassing.
- Imports cleanup: this phase may legitimately re-introduce
  `AlertSummary` to `runtime.py` (for the `_SummarizerProto.summarize`
  return type). Adjust Phase 1's L4 cleanup accordingly — `AlertSummary`
  becomes used again, so do not delete it in Phase 1.

**Verifier**: all existing tests pass without modification (Protocol
typing is structural; `Mock(...)` and `MagicMock()` instances continue to
satisfy the Protocols at runtime). `mypy --strict` (if/when run) shows
no new errors.

### Phase 6 — Documentation / lessons / plan-doc updates

Targets: **M1 (doc-only), lessons.md additions, plan-doc updates**.

- **M1 (defer code change; apply doc update only)** —
  - The code uses `OPSMITRA_LOG_PATH` (verified in `config.py`/`cli.py`).
  - The Step 7 plan and `lessons.md` refer to `OPSMITRA_LOCAL_EVENTS_PATH`.
  - **Implementation is ground truth.** Update the docs to match:
    - Edit `.claude/plans/step7-aws-s3-athena.md` §7 env-var table:
      `OPSMITRA_LOCAL_EVENTS_PATH` → `OPSMITRA_LOG_PATH`.
    - Edit `.claude/tasks/lessons.md` AWS Integration section: same rename.
    - Verify `config.py` / `cli.py` docstrings already say
      `OPSMITRA_LOG_PATH` (they should — they're the source of truth).
  - **No source code rename.** The env var name stays `OPSMITRA_LOG_PATH`.
- Lessons.md additions (under "Coding Lessons"):
  1. **Implementation wins as ground truth on doc/code drift.**
     "When a plan references an env var name that disagrees with the
     implementation, update the plan — not the code — unless the code
     name is actually misleading or ambiguous. `OPSMITRA_LOG_PATH` is
     fine; the Step 7 plan was the document that drifted."
  2. **Runtime-layer guards beat per-iteration guards for config errors.**
     "Validate config-vs-mode invariants in `__init__`, not deep in the
     per-anomaly loop, so the failure mode is one clear
     `RuntimeConfigurationError` instead of N redacted per-anomaly
     errors. See H2 fix in Step 8."

The lessons.md additions are the **only** lessons work in this fix pass.
Other lessons captured during the broader Step 8 workflow happen in the
COMPLETE phase (Step 9 / update-docs), not this fix pass.

**Verifier**: `grep -r OPSMITRA_LOCAL_EVENTS_PATH .claude/ src/ tests/`
returns empty after the doc update; `grep -r OPSMITRA_LOG_PATH .claude/
src/ tests/` finds it consistently used. The two new lesson entries are
present under the appropriate sections in `lessons.md`.

---

## 3. New & Updated Tests — Consolidated List

Only **one strictly required new test** beyond import cleanups. All other
fixes either preserve existing test coverage (refactors, comments,
docstrings, log field renames) or update test bodies in-place.

### 3.1 `tests/test_runtime.py` — +1 test (H2)

1. `test_runtime_rejects_send_mode_without_webhook` —
   - Construct `AlertConfig(slack_webhook_url=None, dry_run=False, …)`.
   - Build `SlackAlerter(alert_cfg)`.
   - Call `Runtime(source=…, summarizer=…, alerter=alerter, …,
     dry_run=False)`.
   - Expect `RuntimeConfigurationError`.
   - Assert message: contains `"dry_run"`; does **not** contain `"https://"`
     or `"hooks.slack.com"`.

### 3.2 `tests/test_cli.py` — +1 test (H1)

2. `test_python_dash_m_entry_point_invokes_main` —
   - Use `subprocess.run([sys.executable, "-m", "opsmitra", "--version"],
     capture_output=True, text=True, check=False)`.
   - Assert `result.returncode == 0`.
   - Assert `__version__` string appears in `result.stdout`.

Optional smoke test (if M2 help-text is grep-asserted):

3. `test_help_text_documents_exit_codes` —
   - Build the parser, call `parser.format_help()`.
   - Assert `"Exit codes"` substring is present.
   - Assert `"check stderr"` substring is present.
   - This is a small smoke check on the M2 documentation update.

### 3.3 Updated tests (in-place body changes only)

- `tests/test_cli.py` — remove unused `io`, `json`, `sys`, `MagicMock`
  imports (L4); existing tests bodies unchanged.
- `tests/test_runtime.py` — remove unused `MagicMock` import (L4); if any
  test asserts on `result.errors[0] == "'RuntimeError'"` (M5 fallback
  shape), update to `"RuntimeError"`. Grep first; this may be zero edits.
- `tests/test_runtime_logging.py` — if any test grep-asserts on
  `errors=<n>` log substring (L2 rename), update to `error_count=<n>`.
  Likely zero edits (logging tests typically check `event=runtime_done`
  presence only).

### 3.4 No new tests required for

- **M3** (TODO marker), **M4** (errors tuple conversion — existing
  `len(result.errors)` assertions work unchanged), **M5** (string-shape
  change covered by existing alert-failure tests if any), **L1** (log
  helper fold — `event=summarizer_failed` already covered by existing
  logging tests), **L4** (unused imports — covered by `ruff F401`),
  **L5** (Protocol typing — runtime-checkable; existing
  `Mock`/`MagicMock` collaborators continue to satisfy), **N1**
  (parsed-once refactor — observable behavior unchanged), **N2**
  (tz-aware enforcement — existing cooldown tests already pass tz-aware
  `datetime` objects; one targeted negative test optional but not
  required), **N3** (docstring), **N4** (comment), **L3** (docstring).

### 3.5 Optional / nice-to-have (not required by acceptance)

- A negative test for **N2** —
  `test_should_alert_rejects_naive_datetime` — would prove the new
  ValueError, but the project's testing convention treats one-line
  invariant guards as covered by the change itself. Skip unless coverage
  on `cooldown.py` drops below the floor.

---

## 4. Acceptance

After the FIX phase runs and RE-VERIFY completes:

1. **All 4 Step 8 acceptance criteria still PASS** (re-verified verbatim
   per `.claude/plans/step8-scheduler-runtime.md` §1):
   - AC1 — End-to-end local demo path still produces a `RuntimeResult` and
     prints the JSON summary; `python -m opsmitra run …` now works too
     (H1).
   - AC2 — Exit codes 0 / 1 / 2 still distinguish the documented states;
     exit 2 ambiguity acknowledged in `--help` (M2 doc).
   - AC3 — Structured logging unchanged (L1 folds the lone bypass into
     `_log_event`; L2 renames `errors` → `error_count` for consistency).
   - AC4 — Cooldown fingerprint + suppression unchanged; tz-aware guards
     (N2) only tighten existing implicit contract.
2. **Test suite green**:
   - Existing **190 tests** + **1 required new** (H2) +
     **1 entry-point test** (H1) + optionally 1 help-text smoke (M2).
   - Total: **≥ 191 passing**, zero removed.
3. **Coverage floors** (matching or exceeding Step 8 baseline):
   - `src/opsmitra/cooldown.py` ≥ **94%**
   - `src/opsmitra/runtime.py` ≥ **92%**
   - `src/opsmitra/cli.py` ≥ **86%**
   - `src/opsmitra/config.py` ≥ **95%**
   - Suite total ≥ **95%**
4. **No regression on Steps 1–7** — all baseline tests from prior steps
   remain green.
5. **`ruff` + `black` clean** on `src/` and `tests/`. `ruff F401` reports
   zero unused imports.

---

## 5. Risks

| Phase | Risk | Mitigation |
|------|------|------------|
| 1 | L4 import removal hides a transitive re-export some external caller relies on. | All removed imports are confined to `src/opsmitra/` internals and `tests/`. No public API change. `ruff F401` already catches transitive use. |
| 1 | L2 log field rename breaks a downstream log consumer that greps for `errors=<n>`. | OpsMitra v0 has no documented log consumer contract. The rename aligns log + JSON-summary on `error_count`, which is the cleaner invariant. |
| 2 | M4 tuple conversion breaks a caller that mutates `result.errors`. | `RuntimeResult` is `frozen=True`; mutation was already a latent contract violation. Grep `tests/` for `.errors.append(` and `.errors[0] = ` (expected: zero hits). |
| 2 | M5 string-shape change breaks a test asserting on `"'RuntimeError'"`. | Pre-edit grep + per-test review; touch only the assertions that fail. |
| 2 | N1 parsed-once refactor accidentally drops the friendly-error path in `main()`. | The pre-flight `_parse_iso_timestamp` calls remain; only the second parse in `_compose_and_execute` is removed. Existing `test_run_subcommand_exit_one_bad_timestamp` test continues to cover. |
| 3 | H1 `__main__.py` breaks if `cli.main()` signature changes. | `cli.main()` is the project's documented entry point; signature drift is unlikely. The 2-line file is the minimal viable shim. |
| 3 | M2 help-text change reformats the existing test's expected stdout. | The description change is additive; any existing help-text test should be updated to match. If no test exists, no risk. |
| 4 | H2 guard breaks `Runtime` construction in any test that historically built `dry_run=False` without a webhook. | Pre-edit grep for `dry_run=False` test constructors; update to either pass a stub webhook URL or stay in `dry_run=True`. The H2 test is the new authoritative coverage of this invariant. |
| 5 | L5 Protocols subtly reject a duck-typed test fake that lacks the required method signature. | `@runtime_checkable` defers to attribute presence at runtime, so `Mock`/`MagicMock` (which auto-respond to any attribute) still satisfy. Pre-edit, grep for any non-Mock fake collaborator and verify it has `summarize` / `send` methods. |
| 6 | M1 doc-only change misses an instance of the old env-var name in a forgotten file. | `grep -r OPSMITRA_LOCAL_EVENTS_PATH` post-edit returns empty. |

---

## 6. Deferred Items (Step 10 Hardening candidates)

| ID  | Item                                                            | Why deferred                                                                                                       | Target |
|-----|-----------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|--------|
| M1  | `OPSMITRA_LOG_PATH` ↔ `OPSMITRA_LOCAL_EVENTS_PATH` env-var rename | Implementation is ground truth; renaming code would cascade through tests + multiple lessons.md entries. Update docs in this pass instead (Phase 6). | Doc-only this pass — no code rename targeted |
| M2  | Exit code 2 disambiguation (anomalies vs argparse error)         | Full remap to EX_USAGE (64) needs an argparse subclass override of `error()` and would touch every CLI test. Acceptable v0 ambiguity. | Step 10 |
| L3  | Cooldown `fcntl` file lock for concurrent Runtimes              | Single-process v0 backend per Step 8 plan §9; concurrent runtimes are explicitly out of scope. Docstring warning sufficient. | Step 10 |
| N4  | `fh.flush()` + `os.fsync()` before `os.replace` in `persist()` | Cooldown is performance optimization, not correctness; cross-crash durability does not affect alert correctness. | Step 10 |

**Note**: M1 receives a documentation update *this pass* (Phase 6). M2
receives a `--help` text update *this pass* (Phase 3). L3 and N4 receive
docstring/comment markers *this pass* (Phase 1). All four code-level
changes are deferred to Step 10.

---

## 7. Lessons to Capture (during this fix pass — Phase 6)

Two lessons are added to `.claude/tasks/lessons.md` as part of Phase 6.
These are **load-bearing for the fix pass**, not deferred to Step 9
update-docs, because they directly justify the M1 deferral and the H2
design choice.

> **Implementation wins as ground truth on doc/code drift.** When a plan
> references an env var name that disagrees with the implementation,
> update the plan — not the code — unless the code name is actually
> misleading or ambiguous. `OPSMITRA_LOG_PATH` is fine; the Step 7 plan
> was the document that drifted. *(Justifies M1 deferral.)*

> **Runtime-layer guards beat per-iteration guards for config errors.**
> Validate config-vs-mode invariants in `__init__`, not deep in the
> per-anomaly loop, so the failure mode is one clear
> `RuntimeConfigurationError` instead of N redacted per-anomaly errors.
> *(Justifies H2 design choice.)*

Any other lessons surfaced during the Step 8 workflow (e.g. from the
COMPLETE phase) go into `lessons.md` during Step 9 (update-docs), not
this fix pass.

---

## 8. FIX-AGENT INSTRUCTIONS (sonnet)

**Read this section verbatim. Apply edits phase-by-phase, run the
post-flight check after each phase, then stop.**

### 8.1 Pre-flight

1. Confirm branch `dev`:
   ```bash
   cd /Users/bhushan/Coding/Projects/OpsMitra && git rev-parse --abbrev-ref HEAD
   ```
2. Confirm target files exist:
   - `src/opsmitra/runtime.py`, `cli.py`, `cooldown.py`, `config.py`,
     `event_source.py`, `slack_alerter.py`, `summarizer.py`
   - `tests/test_runtime.py`, `test_cli.py`, `test_runtime_logging.py`,
     `test_cooldown.py`, `test_config.py`
   - `.claude/plans/step7-aws-s3-athena.md`
   - `.claude/tasks/lessons.md`
3. Confirm `src/opsmitra/__main__.py` does NOT exist (it will be created
   in Phase 3).

### 8.2 Edit order (6 phases)

Apply in this exact order. After each phase, run §8.3 quick check.

1. **Phase 1** — L4 (imports), L2 (log field), N3 (docstring), N4
   (comment), L3 (docstring), M3 (TODO marker).
2. **Phase 2** — M5 (drop repr), L1 (fold log helper), N1 (parse-once),
   N2 (tz-aware guards), M4 (errors tuple).
3. **Phase 3** — H1 (`__main__.py`), M2 (`--help` description).
4. **Phase 4** — H2 (`Runtime.__init__` guard + new test + Step 8 plan
   sentence update).
5. **Phase 5** — L5 (Protocols).
6. **Phase 6** — M1 (Step 7 plan + lessons.md env-var rename) + two new
   lessons.md entries.

### 8.3 Per-phase quick check

```bash
cd /Users/bhushan/Coding/Projects/OpsMitra
ruff check src/ tests/
pytest -q
```

If either fails, stop and report the failing output. Do **not** "fix it
harder" by adding new code paths.

### 8.4 Final post-flight

```bash
cd /Users/bhushan/Coding/Projects/OpsMitra
ruff check src/ tests/
black --check src/ tests/
pytest -q --cov=src --cov-report=term-missing
```

Required outcomes:
- `ruff check`: no errors, zero F401.
- `black --check`: clean.
- `pytest`: ≥ 191 passing (190 baseline + 1 required + ≥ 1 from H1/M2).
- `cooldown.py` ≥ 94%, `runtime.py` ≥ 92%, `cli.py` ≥ 86%,
  `config.py` ≥ 95%, suite ≥ 95%.

### 8.5 Stop conditions

After successful post-flight, report:
- The list of files edited per phase.
- The `pytest` summary line.
- The coverage % for each of the 4 modules above.
- Do **NOT** commit. Do **NOT** push. Do **NOT** advance to RE-VERIFY.
  The main session will run RE-VERIFY via the `verify` skill next.

---

## 9. Out of Scope for This Fix Pass

- The 4 deferred review findings as code changes (see §1.2 and §6); only
  documentation/comment markers are applied this pass.
- Any new features, refactors, or doc updates beyond what is enumerated
  above (e.g. no DynamoDB cooldown backend, no EX_USAGE remap, no
  threshold-wiring into `detect_anomalies`).
- Coverage uplift beyond the floors in §4.
- Commit / push / PR — those happen only after RE-VERIFY and user
  confirmation.

---

## 10. Ready-for-FIX Checklist

- [x] All 12 applied findings have a precise file:line target.
- [x] Each finding is grouped into one of 6 phases.
- [x] New tests enumerated (1 required H2, 1 entry-point H1, 1 optional
      M2 smoke).
- [x] Deferred items listed with rationale and doc-update plan (4 items,
      §6).
- [x] Acceptance bar specifies test count, coverage floors, and AC re-check.
- [x] Risks enumerated per phase.
- [x] FIX-agent instructions are sequenced, phase-bounded, and unambiguous.
- [x] Two lessons captured *in this fix pass* (Phase 6); broader lessons
      deferred to Step 9 update-docs.
- [ ] **User confirmation** to proceed to FIX phase (sonnet agent).

**WAITING FOR CONFIRMATION**: Proceed to Step 8 FIX with this plan?
(yes / no / modify)
