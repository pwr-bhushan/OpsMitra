# Mitra Build Plan

**Project name**: Mitra
**Purpose**: Build a private AI-assisted AWS log anomaly detector for learning AWS, local/private model integration, alerting, and AI-native backend architecture.
**Primary principle**: Deterministic code detects anomalies. The local model explains, prioritizes, and recommends actions.

## Scope

Mitra v0 will use synthetic SaaS logs first, then add AWS ingestion/querying. It will not process production logs, auto-disable customer accounts, or rely on an LLM as the primary anomaly detector.

## Step 1: Define Product Contract and Architecture

Design the Mitra v0 system contract, including event schema, anomaly schema, detector responsibilities, local model responsibilities, and AWS deployment boundaries. Decide the local-first architecture and document why detection stays deterministic while the model only explains evidence.

Acceptance:
- Architecture document explains local simulation, AWS data flow, detector service, model summarizer, and alert delivery.
- Event and anomaly JSON contracts are specified.
- Out-of-scope items are explicit.

## Step 2: Create Python Project Skeleton

Create the Python project structure for Mitra with package layout, CLI entry points, configuration loading, typed domain models, and pytest setup. Keep the code local-only in this phase.

Acceptance:
- `pytest` runs successfully.
- Package imports work from tests.
- Config supports local paths and future AWS settings without hardcoding secrets.

## Step 3: Build Synthetic Log Generator

Implement a synthetic multi-tenant SaaS log generator that emits normal traffic plus injectable incidents. Include scenarios for SMS/API abuse, auth failure bursts, API error-rate spikes, and cost/runaway usage.

Acceptance:
- Generator creates deterministic output with a seed.
- Generated events match the event schema.
- Tests prove each incident type can be injected.

## Step 4: Implement Deterministic Detectors

Implement anomaly detectors for SMS/API abuse spike, auth failure burst, endpoint error-rate spike, and cost/runaway usage. Detectors must emit structured anomaly JSON with evidence fields and recommended actions.

Acceptance:
- Unit tests cover true positive and normal-baseline cases.
- Each detector works without any model call.
- Anomaly output includes severity, observed value, baseline, ratio, evidence, and recommended actions.

## Step 5: Add Local Model Summarization

Integrate a local/private model provider abstraction, starting with Ollama-compatible HTTP. Convert structured anomaly JSON into concise Slack-ready explanations while validating model output shape.

Acceptance:
- Summarizer works with a mocked model client in tests.
- Prompt includes scoped evidence only, not raw full logs.
- Model output has a validated fallback if the local model is unavailable.

## Step 6: Add Slack Alert Delivery

Implement Slack webhook alert delivery with dry-run mode, formatted messages, retry-safe behavior, and no secret leakage in logs. Keep Teams support as a later extension point.

Acceptance:
- Alert formatting is tested without sending real messages.
- Webhook URL is read from environment/config only.
- Dry-run mode prints the final alert payload.

## Step 7: Add AWS S3 and Athena Integration

Add optional AWS mode that writes generated logs to S3, defines an Athena-compatible table shape, and runs partition-aware queries for detector input. Keep local mode fully functional.

Acceptance:
- AWS code is isolated behind interfaces.
- Athena query text is tested for partition filters.
- Local tests do not require AWS credentials.

## Step 8: Add Scheduler and Runtime Command

Create a runnable detector command that can execute on a time window, read from local files or AWS Athena, produce anomalies, summarize them, and send or dry-run alerts. Prepare it for EventBridge plus Lambda/ECS scheduling.

Acceptance:
- CLI can run a complete local demo from generated logs to dry-run alert.
- Exit codes distinguish success, detected anomalies, and runtime failure.
- Runtime logs are structured and safe.

## Step 9: Build Evaluation Harness

Create replay tests and evaluation reports for known injected incidents. Measure detection success, false positives on normal traffic, alert quality, and rough runtime/query cost.

Acceptance:
- Evaluation command runs against fixture datasets.
- Report includes detected, missed, false positive counts, and detection delay.
- Tests fail when known critical incidents are missed.

## Step 10: Security, Cost, and Documentation Hardening

Review IAM boundaries, secret handling, model data exposure, Athena cost risks, and operational failure modes. Update README with setup, local demo, architecture, and AWS deployment notes.

Acceptance:
- Security review checklist is documented.
- README can guide a new developer through local demo.
- AWS cost controls and partitioning assumptions are documented.
