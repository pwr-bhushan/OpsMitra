# Step 8 Plan — Scheduler and Runtime Command

**Status:** PLAN (awaiting implementation)
**Branch:** `dev`
**Acceptance source:** `.claude/plans/opsmitra-build-plan.md` lines 76–84

---

## 1. Requirements Restatement

| AC | Subgoal | Testable assertion |
|----|---------|--------------------|
| AC1 | CLI can run end-to-end local demo from generated logs to dry-run alert | `opsmitra run --source local --dry-run --window-start ... --window-end ...` exits cleanly with `RuntimeResult` printed; no network I/O |
| AC2 | Exit codes distinguish success / detected anomalies / runtime failure | Exit 0 (clean), 2 (anomalies found), 1 (runtime failure) — covered by `tests/test_cli.py` |
| AC3 | Runtime logs are structured and safe | Every log line is `key=value` form; no webhook URL, no query string, no raw event payload, no `QueryExecutionId` appears in caplog |
| AC4 | Stable fingerprint + cooldown suppresses re-alerts within window | `AnomalyCooldown.should_alert` returns False inside window for same `(type, tenant, subject)`; True after expiry; persistence round-trips through tmp file |

---

## 2. Public Surface (CLI)

Extend `opsmitra run` subcommand (currently a stub):

```
opsmitra run
  --window-start ISO8601          # required
  --window-end ISO8601            # required
  [--tenant TENANT_ID]            # optional filter
  [--types ENDPOINT [ENDPOINT ...]] # optional filter
  [--dry-run | --send]            # mutually exclusive; default: --dry-run
  [--source local|athena]         # default: from config (OPSMITRA_EVENT_SOURCE)
  [--config-file PATH]            # optional thresholds JSON path override
```

**Exit codes:**

| Code | Meaning |
|------|---------|
| `0`  | Run succeeded; **zero** anomalies detected |
| `2`  | Run succeeded; **≥1** anomaly detected (still success in pipeline terms, distinguishable for CI/EventBridge wiring) |
| `1`  | Runtime failure — `RuntimeConfigurationError`, `EventSourceError`, `SummarizerError`, `AlertDeliveryError` (terminal), or unhandled exception |

**Parser:** stdlib `argparse` (consistent with existing `build_parser`). No new CLI deps.

---

## 3. New Module: `src/opsmitra/runtime.py`

### Types

```python
@dataclass(frozen=True)
class RuntimeResult:
    anomalies_detected: int
    anomalies_alerted: int
    anomalies_suppressed: int
    source_kind: str        # "local" | "athena"
    sink_kind: str          # "stdout" | "slack"
    dry_run: bool
    errors: list[str]       # redacted error strings (str of caught exceptions, never raw)
```

### Error hierarchy

```python
class RuntimeError(Exception): ...          # base — distinct from builtins.RuntimeError; use OpsMitraRuntimeError
class RuntimeConfigurationError(RuntimeError): ...
class EventSourceError(RuntimeError): ...
class SummarizerError(RuntimeError): ...
class AlertDeliveryError(RuntimeError): ...
```

(Name `OpsMitraRuntimeError` to avoid shadowing builtin.)

### Runtime composition

```python
class Runtime:
    def __init__(
        self,
        source: EventSource,
        summarizer: Summarizer,
        alerter: SlackAlerter,
        cooldown: AnomalyCooldown,
        thresholds: DetectorThresholds,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_events_per_window: int = 1_000_000,
    ) -> None: ...

    def execute(
        self,
        window_start: datetime,
        window_end: datetime,
        tenant: str | None = None,
        types: Sequence[str] | None = None,
    ) -> RuntimeResult: ...
```

All collaborators are Protocol-typed so tests inject fakes without network/LLM.

---

## 4. New Module: `src/opsmitra/cooldown.py`

### Fingerprint

```python
def fingerprint(anomaly: Anomaly) -> str:
    """Stable: type|tenant|subject-canonical."""
    tenant = anomaly.tenant_id or "unknown"
    # Canonicalize subject: sorted JSON of scalar fields only, never includes secrets
    subject = json.dumps(anomaly.subject, sort_keys=True, separators=(",", ":"))
    return f"{anomaly.type}|{tenant}|{subject}"
```

Never reads webhook URL, never reads `evidence`. Pure function, deterministic.

### CooldownEntry + AnomalyCooldown

```python
@dataclass(frozen=True)
class CooldownEntry:
    fingerprint: str
    last_alerted: datetime   # tz-aware UTC

class AnomalyCooldown:
    def __init__(
        self,
        path: Path,
        window_seconds: int,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None: ...

    def should_alert(self, anomaly: Anomaly, now: datetime | None = None) -> bool: ...
    def record_alerted(self, anomaly: Anomaly, now: datetime | None = None) -> None: ...
    def prune_expired(self, now: datetime | None = None) -> int: ...
    def persist(self) -> None: ...     # write JSON snapshot
    def load(self) -> None: ...        # initialize from disk; silent reset on corruption
```

### Persistence

- v0 backend: JSON file at `OPSMITRA_COOLDOWN_PATH` (default `./.opsmitra/cooldown.json`).
- Format: `{"version": 1, "entries": [{"fingerprint": str, "last_alerted": ISO}, ...]}`.
- File I/O mirrors `slack_alerter.py` patterns: stdlib only, bounded read, fail-soft on corruption.
- **Malformed file → silent re-init** (log warning, do not crash pipeline). Rationale: cooldown is a perf optimization, not correctness; a corrupted file should not block alerting.
- **File-size cap:** 1 MB (matches `_MAX_THRESHOLDS_FILE_BYTES` precedent). Larger → silent reset + warning.

---

## 5. Structured Logging

### Logger

Single `logging.getLogger("opsmitra.runtime")`. All pipeline stages emit through:

```python
def _log_event(level: int, event_type: str, **fields: Any) -> None:
    """Emit key=value structured log line."""
    parts = [f"event={event_type}"] + [f"{k}={v}" for k, v in fields.items()]
    logger.log(level, " ".join(parts))
```

### Hard rules (per Step 7 review precedent + lessons.md)

**NEVER log:**
- Raw `Event` objects (may contain user PII per `Anomaly.evidence` notes)
- Webhook URL (already redacted in `SlackAlerter`; runtime must never inline `config.alert.slack_webhook_url`)
- AWS account info, Athena query strings, `QueryExecutionId`, S3 keys
- Anomaly `evidence` dict (may contain `request_id`, `country`, sampled fields)

**DO log:**
- Counts: `events_fetched`, `anomalies_detected`, `anomalies_alerted`, `anomalies_suppressed`
- Kinds: `source_kind=local|athena`, `sink_kind=stdout|slack`
- Anomaly fingerprints (NOT raw `subject` dict in detail) — fingerprint is already type+tenant+subject-hashed
- `dry_run=true|false`
- Stage elapsed time: `stage=fetch elapsed_ms=42`

### Pipeline stage events (one log per stage)

| Stage | Event type | Fields |
|-------|------------|--------|
| Start | `runtime_start` | `source_kind`, `sink_kind`, `dry_run`, `window_start_iso`, `window_end_iso`, `tenant` |
| Fetch | `events_fetched` | `count`, `elapsed_ms`, `truncated=bool` |
| Detect | `detection_complete` | `anomalies_detected`, `elapsed_ms` |
| Suppress | `anomaly_suppressed` | `fingerprint`, `cooldown_remaining_seconds` |
| Summarize | `summarize_complete` | `fingerprint`, `confidence`, `used_fallback=bool` |
| Alert | `alert_sent` / `alert_failed` | `fingerprint`, `delivered`, `attempts`, `dry_run` |
| Done | `runtime_done` | `result_json_summary` (counts only) |

---

## 6. Pipeline Stages (`Runtime.execute`)

### Stage 1 — Validate

- Inspect injected config; raise `RuntimeConfigurationError` for impossible combinations (e.g. `--send` with no webhook URL when not dry-run).
- Validate `window_start < window_end`, both tz-aware UTC.
- Failure → exit 1.

### Stage 2 — Fetch

```python
events = list(itertools.islice(
    source.fetch_events(window_start, window_end, tenant=tenant, types=types),
    max_events_per_window + 1,
))
truncated = len(events) > max_events_per_window
if truncated:
    events = events[:max_events_per_window]
    _log_event(WARNING, "events_truncated", limit=max_events_per_window)
```

- Wrap `source.fetch_events` in try/except → `EventSourceError`.
- Cost guard: `OPSMITRA_MAX_EVENTS_PER_WINDOW` (default 1_000_000) hard cap. Prevents runaway Athena scans.

### Stage 3 — Detect

**Choice: use the orchestrator (`detect_anomalies`), not the public wrappers.**

Rationale (per Step 7 review M6 + `lessons.md` "KNOWN DIVERGENCE"):
- The orchestrator uses **baseline-ratio defense** — wrappers bypass it.
- Production scheduler must use the more sensitive path; wrappers are intentionally less sensitive and meant for ad-hoc one-off CLI checks.
- Single code path = single source of truth for alerts. Mixing is explicitly forbidden by `lessons.md`.

```python
# detect_anomalies signature: (events, window_start, window_end, config: DetectionConfig | None)
anomalies = detect_anomalies(events, window_start, window_end, config=None)
```

Note: `DetectorThresholds` is NOT consumed by `detect_anomalies`. **Step 8 documents this gap; Step 10 unification is when thresholds will reach the orchestrator.** For now `--config-file` only affects future wrapper paths and prints a notice if specified.

### Stage 4 — Per-anomaly loop

```python
for anomaly in anomalies:
    now = clock()
    if not cooldown.should_alert(anomaly, now):
        _log_event(INFO, "anomaly_suppressed", fingerprint=fingerprint(anomaly))
        suppressed += 1
        continue
    try:
        summary = summarizer.summarize(anomaly)
    except Exception as exc:
        # Summarizer already has internal fallback; this should not happen.
        # If it does, wrap as SummarizerError and record in errors list — DO NOT abort run.
        errors.append(_redact_exc(exc))
        continue
    try:
        result = alerter.send(summary, anomaly)
    except Exception as exc:
        errors.append(_redact_exc(exc))
        continue
    if result.delivered:
        cooldown.record_alerted(anomaly, now)
        alerted += 1
    else:
        errors.append(f"slack_send_failed fingerprint={fingerprint(anomaly)}")
```

### Stage 5 — Persist + Return

```python
cooldown.prune_expired(clock())
cooldown.persist()
return RuntimeResult(
    anomalies_detected=len(anomalies),
    anomalies_alerted=alerted,
    anomalies_suppressed=suppressed,
    source_kind=source_kind,
    sink_kind="slack" if not dry_run else "stdout",
    dry_run=dry_run,
    errors=errors,
)
```

---

## 7. Config Additions (`src/opsmitra/config.py`)

| Env var | Default | Validation |
|---------|---------|-----------|
| `OPSMITRA_COOLDOWN_SECONDS` | `3600` | Must be `>= 0`; ValueError otherwise |
| `OPSMITRA_COOLDOWN_PATH` | `./.opsmitra/cooldown.json` | Path; parent dir created on first persist |
| `OPSMITRA_MAX_EVENTS_PER_WINDOW` | `1_000_000` | Must be `> 0`; ValueError otherwise |

Add fields to `AppConfig` (frozen dataclass):

```python
@dataclass(frozen=True)
class RuntimeConfig:
    cooldown_seconds: int = 3600
    cooldown_path: Path = Path("./.opsmitra/cooldown.json")
    max_events_per_window: int = 1_000_000

@dataclass(frozen=True)
class AppConfig:
    # ...existing...
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
```

Validation lives in a new `_load_runtime_config(values)` helper — bounds-check at load time per `lessons.md` "Numeric config bounds at load time" rule.

---

## 8. CLI Module Changes (`src/opsmitra/cli.py`)

- Replace stub `run_parser` block with full argument set (§2).
- Add `--window-start` / `--window-end` parsing via `datetime.fromisoformat` → tz-aware UTC. Bad timestamp → exit 1 with friendly stderr message.
- Add `_compose_runtime(config, args) -> Runtime` factory: instantiates `EventSource` (local NDJSON or Athena), `Summarizer` (`OllamaClient` or fallback per `model.provider`), `SlackAlerter`, `AnomalyCooldown`.
- AWS isolation: import `opsmitra.aws.*` lazily inside the Athena branch only (matches `event_source.py` invariant — `lessons.md` AWS isolation rule).
- Map `RuntimeResult` → exit code: `0` if `anomalies_detected == 0`; `2` if `> 0 and not errors`; `1` if `errors` is non-empty.
- Pretty-print `RuntimeResult` as a single JSON line to stdout for the demo path (no Rich/Click deps).

---

## 9. Scheduler-Readiness Notes (NOT implemented this step)

- Runtime is designed to be invoked by **EventBridge → Lambda / ECS task**. Each invocation runs **ONE window**. No in-process scheduling loop.
- Cooldown persistence to local disk is sufficient for:
  - Long-running ECS task with persistent volume
  - Lambda with mounted EFS
- **Step 10 hardening item:** swap JSON file cooldown for DynamoDB or Redis when running on stateless Lambda. Documented inline in `cooldown.py` module docstring.

---

## 10. Test Plan (target ≥20 tests)

### `tests/test_runtime.py` (10 tests)

- `test_execute_happy_path_zero_anomalies` — fake source returns events → no anomalies → result is all zeros, no alerts sent
- `test_execute_detects_and_alerts` — fake source produces SMS abuse pattern → 1 anomaly → `alerter.send` called once → `record_alerted` called
- `test_execute_dry_run_no_network` — `dry_run=True` → `alerter.send` returns `delivered=True, dry_run=True` → cooldown still updated
- `test_execute_cooldown_suppresses_second_call` — same fingerprint twice across two `execute` calls within window → second call: 0 alerts, 1 suppressed
- `test_execute_cooldown_expires_after_window` — advance clock past `cooldown_seconds` → second call re-alerts
- `test_execute_source_error_raises_event_source_error` — fake source raises → wrapped as `EventSourceError`
- `test_execute_summarizer_uses_fallback_on_model_failure` — model raises inside `Summarizer.summarize`; fallback kicks in → alert still sent
- `test_execute_alert_delivery_error_recorded_not_raised` — alerter `send` returns `delivered=False` → error in `result.errors`, runtime returns success
- `test_execute_events_truncated_to_max` — source yields more than `max_events_per_window` → events truncated, warning logged
- `test_execute_validates_window` — `window_end <= window_start` → `RuntimeConfigurationError`

### `tests/test_cooldown.py` (8 tests)

- `test_fingerprint_deterministic` — same anomaly twice → same fingerprint
- `test_fingerprint_includes_type_tenant_subject` — varying each component changes the fingerprint
- `test_fingerprint_handles_missing_tenant` — `tenant_id=None` → `"unknown"` token
- `test_should_alert_first_sighting_true` — empty store → True
- `test_should_alert_within_window_false` — record then check inside window → False
- `test_should_alert_after_expiry_true` — record, advance clock past `cooldown_seconds` → True
- `test_persistence_round_trip` — record, persist, reload from same path → state preserved
- `test_malformed_cooldown_file_silent_reset` — write garbage to path → `load()` does not raise; treats store as empty; warning logged

### `tests/test_cli.py` (extend, 4 tests)

- `test_run_subcommand_exit_zero_no_anomalies` — local demo path, no anomalies in window → exit 0
- `test_run_subcommand_exit_two_with_anomalies` — local demo path with anomaly → exit 2
- `test_run_subcommand_exit_one_bad_timestamp` — `--window-start notatime` → exit 1, friendly stderr
- `test_run_subcommand_dry_run_default` — no `--send` flag → `dry_run=True` reflected in `RuntimeResult`

### `tests/test_runtime_logging.py` (4 tests)

- `test_each_stage_emits_structured_log` — caplog at INFO; assert presence of `event=runtime_start`, `event=events_fetched`, `event=detection_complete`, `event=runtime_done`
- `test_logs_never_contain_webhook_url` — configure webhook URL; run pipeline (dry-run); assert URL substring absent from every record
- `test_logs_never_contain_raw_event_payload` — events have `request_id` and `country`; assert neither appears verbatim in logs (fingerprint is OK)
- `test_logs_never_contain_query_or_execution_id` — Athena source path: assert `QueryExecutionId`, `SELECT`, S3 bucket name absent from logs

---

## 11. Phased Order

| Phase | Scope | Files touched |
|-------|-------|---------------|
| A | `AnomalyCooldown` + `fingerprint` + JSON persistence | new `src/opsmitra/cooldown.py`; new `tests/test_cooldown.py` |
| B | Config additions: `RuntimeConfig` + bounds-check + env wiring | edit `src/opsmitra/config.py` |
| C | `Runtime` + `RuntimeResult` + error hierarchy + structured logger | new `src/opsmitra/runtime.py`; new `tests/test_runtime.py`, `tests/test_runtime_logging.py` |
| D | CLI `run` subcommand wiring + exit code mapping + JSON stdout | edit `src/opsmitra/cli.py`; extend `tests/test_cli.py` |
| E | Verify (`everything-claude-code:verify`) + python-review (`everything-claude-code:python-review`) |

---

## 12. Risks / Open Questions (Auto-Defaulted)

| Risk | Decision |
|------|----------|
| Detector path: orchestrator vs wrappers | **Orchestrator** (`detect_anomalies`). Baseline-ratio defense is essential for scheduled runs; wrappers documented as ad-hoc one-off use per `lessons.md` divergence note. Unification deferred to Step 10. |
| Cooldown backend: JSON vs DynamoDB | **JSON file** in v0. DynamoDB deferred to Step 10 when Lambda packaging lands. |
| Multi-window batching | **Out of scope.** One window per `Runtime.execute` call. |
| Concurrency | **Single-threaded synchronous.** Out of scope. |
| Cost guard | **`OPSMITRA_MAX_EVENTS_PER_WINDOW=1_000_000`** hard cap to prevent runaway Athena scans. |
| `--config-file` semantics with orchestrator | Threshold overrides DON'T reach `detect_anomalies` yet. Document this as a known limitation; print warning if `--config-file` is passed; Step 10 will unify. |
| Cooldown persistence path | Default `./.opsmitra/cooldown.json`. Auto-create parent dir on first persist. |
| Cooldown file corruption | Silent reset + warning (cooldown is a perf optimization, not correctness). |

---

## 13. Out of Scope

- In-process scheduler loop (cron-like) — runtime is invoked externally only
- Multi-tenant batching across windows
- Metrics emission (CloudWatch / Prometheus / OpenTelemetry)
- DynamoDB or Redis cooldown backend
- Lambda packaging / deployment scripts (Step 10 candidate)
- Threshold overrides flowing into `detect_anomalies` (Step 10 unification)
- `EventSink` writeback of detected anomalies (separate concern; could be Step 9)

---

## 14. Ready-for-TDD Checklist

- [ ] Phase A: `tests/test_cooldown.py` written first → RED
- [ ] Phase A: `src/opsmitra/cooldown.py` implementation → GREEN
- [ ] Phase B: `config.py` test extensions → RED → GREEN
- [ ] Phase C: `tests/test_runtime.py`, `tests/test_runtime_logging.py` → RED
- [ ] Phase C: `src/opsmitra/runtime.py` → GREEN
- [ ] Phase D: `tests/test_cli.py` extensions → RED → GREEN
- [ ] Coverage check: ≥80% on all new modules
- [ ] Run `everything-claude-code:verify` (sonnet)
- [ ] Run `everything-claude-code:python-review` (opus)

---

**WAITING FOR CONFIRMATION** — user has blanket approval per project workflow. Proceed to Step 2 (TDD).
