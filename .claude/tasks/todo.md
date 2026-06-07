# OpsMitra Task Tracker

## Current Phase: ✅ BUILD PLAN COMPLETE — 10 v0 steps + Step 11 (env validation preflight) shipped

- [x] Add scheduler + runtime CLI with fingerprint cooldown and structured logging.
- [x] Create initial OpsMitra build plan.
- [x] Generate ECC plan-orchestrate prompts.
- [x] Wait for user approval before moving to Step 1.
- [x] Define product contract and architecture docs.
- [x] Create Python project skeleton using TDD.
- [x] Build synthetic log generator.
- [x] Implement deterministic detectors.
- [x] Add local model summarization with Ollama client and deterministic fallback.
- [x] Add Slack alert delivery with dry-run, retry, and redacted logging.
- [x] Add AWS S3 + Athena integration with config-driven detector thresholds.
- [x] Add evaluation harness with fixture datasets, p95 detection delays, and strict-mode CI gating.
- [x] Add security/cost/docs hardening with Mermaid architecture diagrams, threshold unification, durable cooldown lock, and AWS-window replay exercise.

## Planned Phases

- [x] Step 1: Define Product Contract and Architecture
- [x] Step 2: Create Python Project Skeleton
- [x] Step 3: Build Synthetic Log Generator
- [x] Step 4: Implement Deterministic Detectors
- [x] Step 5: Add Local Model Summarization
- [x] Step 6: Add Slack Alert Delivery
- [x] Step 7: Add AWS S3 and Athena Integration
- [x] Step 8: Add Scheduler and Runtime Command
- [x] Step 9: Build Evaluation Harness
- [x] Step 10: Security, Cost, and Documentation Hardening
- [x] Step 11 (post-v0): Env Validation Preflight — `opsmitra validate` subcommand consolidating per-mode required/optional env var checks

## Review Notes

- Step 1 architecture/contracts are drafted and committed.
- Step 2 started after user approval.
- Step 2 Python skeleton created with tests first; pytest coverage passed at 93%.
- Step 3 synthetic generator created with deterministic seeded output and injectable incidents; full pytest coverage passed at 94%.
- Step 4 deterministic detectors created for SMS abuse, auth bursts, endpoint error rates, and cost runaway usage; full pytest coverage passed at 95%.
- Step 5 local model summarization added with Ollama-compatible HTTP client, scoped-evidence prompt, validated fallback, model config via env (`OPSMITRA_MODEL_NAME`, `OPSMITRA_MODEL_URL`, `OPSMITRA_MODEL_TIMEOUT`); 31/31 tests passing at 95% coverage.
- Step 6 Slack alerter added with Block Kit formatting, retry on 5xx/timeout, dry-run default, exact-substring URL redaction, and bounds-checked config; 59/59 tests passing at 94% module coverage.
- Step 7 AWS S3 + Athena integration added with `EventSource`/`EventSink` Protocols, boto3 isolated under `opsmitra.aws.*` (subprocess test enforces zero leaks), partition-pruned Athena query builder with regex injection guards, S3 NDJSON sink with UUID-suffixed keys + 100 MB safety cap, and config-driven `DetectorThresholds` with per-tenant/per-endpoint precedence; 142/142 tests passing at 96% suite coverage.
- Step 8 scheduler + runtime CLI added with `Runtime` orchestrator (EventSource → detectors → Summarizer → SlackAlerter pipeline), `AnomalyCooldown` JSON-backed dedup with tz-aware datetimes, `python -m opsmitra run` entry point, exit codes 0/1/2 distinguishing clean/error/anomalies-detected, and structured logging proven safe (sentinel webhook URL never leaks); 197/197 tests passing at 95% suite coverage.
- Step 9 evaluation harness added with `EvaluationCase`/`CaseResult`/`EvaluationReport` dataclasses, fixture-based replay (3 committed: sms-abuse-baseline, auth-burst-baseline, noise-only), bipartite match with deterministic anomaly sort, p95 nearest-rank for n≥100, `detection_delay_upper_bound_seconds` upper-bound metric, 1 MB fixture cap, `--strict` mode that fails only on `must_detect` misses; 230/230 tests passing at 95% suite coverage.
- Step 10 hardening shipped: 4 Mermaid diagrams (3 in architecture.md, 1 in README), security checklist with least-privilege IAM, AWS cost controls with workgroup byte cap, AWS-window replay exercise template, threshold unification M6 preserving baseline-ratio defense, durable cooldown fcntl lock on `<path>.lock`, argparse exit 64 (EX_USAGE), CLI `--config-file` fully wired for both `run` and `eval` subcommands; 253/253 tests passing at 95% suite coverage. FINAL build plan step complete.
- Step 11 (post-v0) env validation preflight shipped: `opsmitra validate` subcommand with auto-detected mode activation (model/slack/aws-source/aws-sink/runtime/eval), required-vs-optional env var checks, cross-field invariants (dry_run=false ↔ webhook required, EVENT_SOURCE=athena ↔ Athena output location required, EVENT_SINK=s3 ↔ S3 bucket required), webhook URL never appears in rendered output (set/unset only), table + JSON output formats, `--strict` exit-1 CI gating; 294/294 tests passing, validation.py 99%, suite 94%. README + security-checklist.md + aws-window-replay.md updated to reference the command.
