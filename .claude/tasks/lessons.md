# OpsMitra Lessons

## Environment & Tooling (read this FIRST in every session/agent spawn)

- **Project venv is at `.venv/` — pytest and python live there. Always invoke via the full path: `.venv/bin/pytest`, `.venv/bin/python`. NEVER call bare `pytest` or `python` — system PATH versions exist and will silently use the wrong site-packages.**
- Test command: `.venv/bin/pytest -xvs`
- Coverage: `.venv/bin/pytest --cov=src/opsmitra --cov-report=term-missing`
- Python entry point: `.venv/bin/python -m opsmitra ...`
- This rule applies to every subagent — paste the venv path explicitly into agent prompts; spawns don't inherit shell PATH overrides from prior turns.

## Domain Knowledge

- OpsMitra is a learning project, not a startup validation exercise.
- The project name changed from Mitra to OpsMitra because OpsMitra is more descriptive and makes the operations/log-alerting purpose clearer.
- Detection should be deterministic; the local/private model explains and summarizes scoped anomaly evidence.
- v0 starts with synthetic logs before touching production-like AWS data.
- The summarizer talks to a local Ollama-compatible HTTP endpoint via `urllib`; model identity and timeout are config-driven (env vars: `OPSMITRA_MODEL_NAME`, `OPSMITRA_MODEL_URL`, `OPSMITRA_MODEL_TIMEOUT`); ANY model failure routes through `FallbackSummarizer` for deterministic `confidence="low"` output.

### Slack Alerting

- `SlackAlerter` is stateful (holds config + opener); `format_alert(summary, anomaly, channel=None) -> dict` is pure and testable without HTTP.
- Block Kit payload: `blocks` array `[header, context (evidence), section (title+evidence), section (action)]`, `attachments[1]` (fallback), top-level `text` field (fallback for non-Block Kit clients).
- Retry policy: exponential backoff `0.5/1/2s` defaults; retries ONLY on `5xx`, `URLError`, `TimeoutError`; `4xx` and `429` fail fast; total wall-clock cap 15s.
- Dry-run mode defaults to `true` (set `OPSMITRA_SLACK_DRY_RUN=false` to actually send); never sends in CI unless explicit override.
- Webhook URL prefix warning is non-fatal (Enterprise Grid uses alternate domain prefixes); captured but doesn't raise.

### AWS Integration

- Two Protocol contracts enforce extensible event handling: `EventSource.fetch_events(start, end, tenant=None)` returns `list[dict]`, `EventSink.write_events(partition, events)` writes and returns key/size/count tuple. Concrete impls: local (`LocalNDJSONEventSource/Sink`) and AWS (`AthenaEventSource`, `S3NDJSONEventSink` under `opsmitra.aws.*`).
- AWS isolation invariant: `opsmitra.event_source` must NEVER import boto3. Isolation is enforced by a subprocess test asserting `boto3 not in sys.modules` after importing `opsmitra.event_source`.
- Athena partitioning uses 5 string keys: `tenant/year/month/day/hour`. Query builder enumerates partitions per-hour for ≤24h windows, per-day for >24h windows. Reduces full-scan overhead for common operational queries.
- SQL injection defense: regex whitelist `r"[a-zA-Z0-9_\-]+"` for tenant names; enum-set whitelist for event types. Both reject with `ValueError` if matched pattern fails.
- S3 key shape: `s3://{bucket}/{prefix}/tenant={t}/year={Y}/month={M}/day={D}/hour={H}/events-{batch_id}.jsonl.gz` where `batch_id = uuid.uuid4().hex[:12]` prevents accidental overwrites when retrying the same partition.
- Per-partition gzip body is capped at 100 MB (`_MAX_GZIP_BYTES`); a warning is issued when half capacity is reached. Exceeding the cap raises `ValueError`.
- Athena polling: dedicated `AthenaQueryError` distinguishes real query failure (e.g. syntax error) from polling timeout. Both `poll_interval` and `max_polls` are constructor-injectable for testing and operational flexibility.

### Detector Thresholds

- `DetectorThresholds` is a flat dataclass (7 fields: `sms_abuse_threshold`, `auth_burst_threshold`, `endpoint_error_rate`, `cost_runaway_rate`, `cost_runaway_ratio`, `baseline_ratio`, `confidence_floor`) living in `config.py`.
- JSON overrides at `OPSMITRA_THRESHOLDS_PATH` use schema `{"default": {...}, "tenants": {...}, "endpoints": {...}}`. Precedence: **endpoint > tenant > default**. Missing precedence levels fall through.
- File-size cap: 1 MB. Loader translates `FileNotFoundError` → friendly `ValueError` with full config path in the message.
- **STATUS (Step 10 shipped):**
  1. The orchestrator path now accepts `DetectorThresholds` via `Runtime(thresholds=…)`; thresholds flow into `detect_anomalies` via `_config_from_thresholds(...)` for the per-window mapping.
  2. The baseline-ratio defense (5× SMS, 10× cost) is preserved at `DetectionConfig` defaults. It is **not tunable** via `DetectorThresholds` in v0 — an acceptable trade-off documented here.
  3. The public `detect_*` wrappers (`detect_sms_abuse`, `detect_cost_runaway`, etc.) still bypass the ratio defense and remain an anti-pattern for library callers. **Do NOT mix wrapper calls with the orchestrator on the same window.** Use `detect_anomalies(...)` end-to-end.

### Runtime + Scheduler

- `Runtime` composes EventSource Protocol, summarizer (Step 5), SlackAlerter (Step 6), AnomalyCooldown (Step 8 new). All injectable via constructor.
- Pipeline stages: validate config → fetch events (with `max_events_per_window` cap) → run detectors via `detect_anomalies` orchestrator (NOT the public `detect_*` wrappers — orchestrator has baseline-ratio defense) → for each anomaly, check cooldown → summarize (Summarizer with FallbackSummarizer on exception) → send via SlackAlerter → record cooldown → persist cooldown JSON file.
- Stage 1 guard: `Runtime.__init__` raises `RuntimeConfigurationError("OPSMITRA_SLACK_WEBHOOK_URL is required when dry_run is false")` if missing webhook in send mode.
- Structured logging: every stage emits `key=value` log fields through `_log_event` helper. No raw `Event` content, no webhook URL, no AWS account info. Uses `event_type=` field naming.
- CLI exit codes: 0 (clean), 1 (runtime/config error), 2 (anomalies detected OR argparse usage error). Argparse misuse-vs-detection ambiguity is documented in `--help`; full EX_USAGE remap is Step 10.
- `RuntimeResult` is `frozen=True` with `errors: tuple[str, ...]` (immutable). Counts: detected = alerted + suppressed + len(errors).
- `python -m opsmitra` entry point via `__main__.py`; also installable via `[project.scripts]`.

### Anomaly Cooldown

- `fingerprint(anomaly) = f"{type}|{tenant or 'unknown'}|{subject}"` — pure, deterministic, no secrets.
- JSON file backend at `OPSMITRA_COOLDOWN_PATH` (default `./.opsmitra/cooldown.json`).
- Atomic write: tempfile in same dir + `os.replace` (POSIX rename).
- Malformed JSON file → silent reset + WARNING log (cooldown is perf, not correctness).
- Tz-aware datetime enforcement: `should_alert`/`record_alerted`/`prune_expired` raise `ValueError` on naive datetime.
- 1 MB file size cap; concurrent-writer race handled via `fcntl` LOCK_EX NB on a durable named lockfile at `<path>.lock`. Single-process v0; multi-host fanout would require a DynamoDB backend (post-v0).
- `prune_expired` called before `persist` to prevent file growth.

### Evaluation Harness

- Public dataclasses (`ExpectedIncident`, `EvaluationCase`, `CaseResult`, `EvaluationReport`) all frozen with tuple fields for immutability.
- Fixture format: paired `<name>.events.jsonl` + `<name>.expected.json`, stem-paired in `tests/fixtures/evaluation/` directory.
- Match key: `(type_norm, tenant_norm, subject_norm)`. Tenant `"*"` is wildcard. Subject is subset-match (`expected ⊆ anomaly`). Matching is case-insensitive for all components.
- Greedy bipartite match in expected declaration order; anomalies pre-sorted by `(type, tenant, subject)` tuple for determinism.
- Detection delay reported as UPPER BOUND: `window_end - first_seen` (intentionally conservative to warn on-call engineers). Field named `detection_delay_upper_bound_seconds` for semantic honesty.
- p95 percentile uses `statistics.quantiles(..., n=100, method='inclusive')[94]` for sample size n ≥ 100; nearest-rank-floor for smaller samples.
- 1 MB fixture file cap enforced at `load_case` time; matches cooldown/thresholds size-budget precedent.
- `evaluate_case` bypasses `Runtime` entirely — no cooldown side effects, no state mutations on the user's persistent data.
- Detector path: `detect_anomalies` orchestrator (NOT public `detect_*` wrappers; orchestrator has baseline-ratio defense per Step 7 M6).
- CLI: `python -m opsmitra eval [--dataset PATH] [--report json|table] [--output PATH] [--strict]`. Exit codes: 0 (clean), 2 (non-strict mode + any miss), 1 (strict mode + critical miss OR runtime error).

### Security & Hardening

- Least-privilege IAM policy in `docs/security-checklist.md`: Athena actions scoped to `StartQueryExecution`/`GetQueryExecution`/`GetQueryResults` on the workgroup resource; S3 actions scoped to `PutObject`/`GetObject`/`ListBucket` on the specific bucket + prefix ARN. No `s3:*` or `athena:*` wildcards.
- Secret handling: webhook URL consumed only via `OPSMITRA_SLACK_WEBHOOK_URL` env var; `_redact(url, text)` strips it in `slack_alerter.py`; `AthenaQueryError` carries only query-exec-ID and status string — no SQL text, no account info; summarizer prompt is scoped evidence string ≤480 chars only.
- AWS cost controls: workgroup `BytesScannedCutoffPerQuery` set to 1 GB in `docs/aws-cost-controls.md`; partition-predicate is mandatory at query_builder level (raises `ValueError` if partition keys are missing); 100 MB gzip cap per S3 partition (`_MAX_GZIP_BYTES` in `s3_sink.py`); `OPSMITRA_MAX_EVENTS_PER_WINDOW` runtime guard caps events fed to detectors.
- AWS-window replay exercise documented in `docs/aws-window-replay.md` as a repeatable checklist (setup → run → collect → tune → conclusions); findings template at `docs/aws-window-replay-findings.md` (9 ## sections including Detectors Triggered, False Positives, False Negatives, Threshold Adjustments, Conclusions).
- Mermaid: 4 diagrams in GitHub-flavored Markdown — D1 flowchart (pipeline overview), D2 sequence (Runtime execute), D3 flowchart (eval harness flow) in `docs/architecture.md`; D4 flowchart (overview) in `README.md`. Render natively in GitHub UI.

## Coding Lessons

- Always do OpsMitra project changes on the `dev` branch, not `main`.
- When the user renames the project, update user-facing docs, plans, trackers, and lessons consistently.
- Keep AWS-dependent code behind interfaces so local tests do not require AWS credentials.
- Do not send full raw logs to model prompts; pass scoped anomaly evidence only.
- When extending TDD-first contracts (existing untracked test files like `test_summarizer.py`), treat them as locked public contracts: extend, do not rewrite.
- Malformed config inputs should raise `ValueError` to surface misconfiguration loudly, rather than silently defaulting.
- **Secret redaction in exception strings:** When the same secret (e.g. webhook URL) can leak via multiple `except` branches, redact `str(exc)` BEFORE branching on exception type — reordering subclass exceptions (e.g. `HTTPError` ⊂ `URLError`) silently turns a safe branch into a leak point.
- **Numeric config bounds at load time:** Validate every numeric field (timeout, retry count, backoff cap) during `_load_alert_config()`, not at first use — field-named error messages turn config bugs into 1-line fixes; lazy validation turns them into 3am debugging sessions.
- **Parallel config dataclasses and semantic divergence:** When introducing a new config dataclass that overlaps with an existing one (e.g. `DetectorThresholds` and `DetectionConfig`), write a unifying docstring warning AND a Step 10 deferral note immediately, or risk silent semantic divergence between two parallel public surfaces. The wrappers will not share the same baseline-ratio defense as the orchestrator; document this explicitly.
- **Hive-partitioned S3 sinks need unique batch suffixes:** Always use a per-write unique suffix (e.g. batch UUID) in the object key. Reusing the same partition path silently overwrites prior data, and partitions encode no batch identity by design. Silent overwrites are much harder to debug than loud partition-collision errors.
- **Implementation wins as ground truth on doc/code drift.** When a plan references an env var name that disagrees with the implementation, update the plan — not the code — unless the code name is actually misleading or ambiguous. `OPSMITRA_LOG_PATH` is fine; the Step 7 plan was the document that drifted. (Justifies M1 deferral in Step 8 fix.)
- **Runtime-layer guards beat per-iteration guards for config errors.** Validate config-vs-mode invariants in `__init__`, not deep in the per-anomaly loop, so the failure mode is one clear `RuntimeConfigurationError` instead of N redacted per-anomaly errors. See H2 fix in Step 8.
- **Use Protocol typing on runtime orchestrator collaborators.** When building a runtime orchestrator that composes 4+ collaborators, use Protocol typing on each constructor parameter — both for `@runtime_checkable` runtime safety AND for mypy clarity. `Any` collaborators erase both signals; the small Protocol cost pays back the moment a fake breaks the contract.
- **Documented-but-dead parameters violate YAGNI.** If a parameter is documented as "accepted for API compatibility" but never used, REMOVE it. Future readers will assume it does something. *(Justifies M1 removal in Step 9 fix.)*
- **Metric names must be semantically honest.** `detection_delay_upper_bound_seconds` is harder to type than `detection_delays_seconds`, but it tells the on-call engineer what the number means. Conservative metrics with optimistic names hide bugs. *(Justifies M2 rename in Step 9 fix.)*
- **Mirror runtime-config size-budget discipline in fixture files.** When committing fixture files for CI, use the same size-budget discipline as runtime config files. 1 MB cap with a field-named `ValueError` matches the cooldown / thresholds precedent. *(Justifies H2 cap + error shape in Step 9 fix.)*
- **When unifying parallel APIs, default-preserving identity mappings beat opinionated re-mappings.** Setting `sms_ratio_threshold=1.0` in the `_config_from_thresholds` mapper disabled the very baseline-ratio defense the unification was meant to preserve. When bridging API A → API B, only forward the fields that A explicitly exposes; let B's defaults stand for everything else. *(Justifies C1 fix in Step 10.)*
- **`fcntl` advisory locks must target a stable, named lockfile, not a per-call temp path.** A lockfile created by `tempfile.mkstemp` is unique per call, so two concurrent writers never collide on it → the flock is a no-op. The lock target must be a durable named file (`<path>.lock`) co-located with the protected resource. Verify mutual exclusion by simulating a concurrent lock-holder in the test, not by trusting the `flock` call alone. *(Justifies M2 fix in Step 10.)*
