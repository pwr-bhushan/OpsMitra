# Step 5: Add Local Model Summarization — Implementation Plan

**Build Plan reference**: `.claude/plans/opsmitra-build-plan.md` → Step 5
**Branch**: `dev` (mandatory per `lessons.md`)
**Workflow phase**: PLAN (no code in this phase)

---

## 1. Requirements Restatement

Add a local-first summarization layer that turns deterministic `Anomaly` records
(produced by `opsmitra.detectors`) into Slack-ready `AlertSummary` records using
a local/private model (Ollama-compatible HTTP). The model never sees raw logs —
only scoped anomaly evidence. If the model is unavailable, returns malformed
output, or times out, a deterministic `FallbackSummarizer` is used instead.

The public contract is already pinned by the existing TDD file
`tests/test_summarizer.py` and **must not be changed**. Specifically:

- Module `opsmitra.summarizer` must export: `AlertSummary`, `ModelClient`,
  `Summarizer`, `FallbackSummarizer`.
- `ModelClient` is a `Protocol` with one method: `complete(prompt: str) -> str`.
- `Summarizer(ModelClient).summarize(Anomaly) -> AlertSummary` parses JSON and
  validates the model output, falling back on failure.
- `AlertSummary` is a frozen dataclass with exactly these 6 fields:
  `title`, `severity`, `summary`, `likely_cause`, `recommended_action`,
  `confidence`.
- `FallbackSummarizer().summarize(Anomaly) -> AlertSummary` is deterministic and
  uses no model call.

The prompt body must include the substring `req_1` (sample request id) and must
never include the phrase `raw full logs`, per the existing stub assertion.

---

## 2. File Layout & Module Responsibilities

### 2.1 New file: `src/opsmitra/summarizer.py`

Single new module. Layout, top to bottom:

1. **Module docstring + imports**
   - `from __future__ import annotations`
   - stdlib only: `json`, `urllib.request`, `urllib.error`, `dataclasses`,
     `typing` (`Protocol`, `runtime_checkable`), `logging`.
   - `from opsmitra.models import Anomaly`
   - `from opsmitra.config import ModelConfig`
   - `logger = logging.getLogger(__name__)`

2. **Constants (module-level, no hardcoded model names)**
   - `_VALID_SEVERITIES = {"low", "medium", "high", "critical"}`
   - `_VALID_CONFIDENCES = {"low", "medium", "high"}`
   - `_REQUIRED_FIELDS = ("title", "severity", "summary", "likely_cause",
     "recommended_action", "confidence")`
   - `_OLLAMA_GENERATE_PATH = "/api/generate"`
   - No string literal `"llama3.1:8b"` anywhere in this file — model name comes
     from config only.

3. **`AlertSummary` dataclass**
   ```text
   @dataclass(frozen=True)
   class AlertSummary:
       title: str
       severity: str
       summary: str
       likely_cause: str
       recommended_action: str
       confidence: str
   ```
   Frozen for immutability (per `rules/common/coding-style.md`).
   Equality is used by `test_fallback_summarizer_is_deterministic`.

4. **`ModelClient` Protocol**
   ```text
   @runtime_checkable
   class ModelClient(Protocol):
       def complete(self, prompt: str) -> str: ...
   ```
   Runtime-checkable so the existing tests' `StubClient(ModelClient)` /
   `BrokenClient(ModelClient)` subclass syntax keeps working.

5. **`OllamaClient` (concrete `ModelClient`)**
   - Constructor: `__init__(self, model_name: str, base_url: str,
     timeout_seconds: float)` — all three injected, no defaults.
   - Method: `complete(self, prompt: str) -> str` — POSTs JSON to
     `f"{base_url}{_OLLAMA_GENERATE_PATH}"` using `urllib.request.Request`
     with `Content-Type: application/json`, body
     `{"model": self.model_name, "prompt": prompt, "stream": False,
     "format": "json"}`, hard timeout = `self.timeout_seconds`.
   - Reads response, parses outer Ollama envelope, returns the
     `"response"` field as a `str`.
   - Raises on any non-2xx, timeout, or JSON envelope error so `Summarizer`
     can catch and fall back.
   - Convenience factory: `@classmethod from_config(cls, cfg: ModelConfig,
     timeout_seconds: float) -> "OllamaClient"`.

6. **`FallbackSummarizer`**
   - No constructor args, no `ModelClient` dependency.
   - `summarize(self, anomaly: Anomaly) -> AlertSummary`:
     - `title = f"{anomaly.severity.capitalize()} {anomaly.type} detected"`
     - `summary` includes `anomaly.tenant_id` literally (test asserts
       `"tenant_acme" in summary.summary`). Concrete form:
       `f"Tenant {anomaly.tenant_id} triggered {anomaly.type} between
       {window_start_iso} and {window_end_iso}."`
       If `tenant_id is None`, substitute the string `"unknown"` — but the
       existing test passes a non-None tenant, so the None branch is for
       robustness only.
     - `likely_cause`: derived from `anomaly.evidence["notes"][0]` if
       present, else `"Deterministic detector evidence — see anomaly record."`
     - `recommended_action`: `anomaly.recommended_actions[0]`
       (test asserts equality to `"Temporarily throttle the API key"`).
       Guarded by an explicit length check (the `Anomaly` constructor already
       guarantees non-empty, but we add a defensive check).
     - `severity = anomaly.severity` (validated to be in `_VALID_SEVERITIES`).
     - `confidence = "low"` (test asserts).
   - Deterministic: pure function of `Anomaly` fields, no clocks, no RNG.

7. **`Summarizer`**
   - Constructor: `__init__(self, client: ModelClient,
     fallback: FallbackSummarizer | None = None)`.
     Default `fallback = FallbackSummarizer()` constructed lazily inside
     `__init__` so equality of summarizer instances isn't required.
   - Method: `summarize(self, anomaly: Anomaly) -> AlertSummary`:
     1. Build prompt via `_build_prompt(anomaly)`.
     2. `try: raw = self.client.complete(prompt)` — catch
        `Exception` broadly (network, timeout, HTTP, decode) and route to
        fallback. Log at `WARNING` with anomaly id and exception type only
        (no secrets, no full prompt).
     3. `parsed = _parse_and_validate(raw)` — returns `AlertSummary` or
        raises `ValueError`.
     4. On any `ValueError`, return `self._fallback.summarize(anomaly)`.

8. **Helpers (module-private)**
   - `_build_prompt(anomaly: Anomaly) -> str` — see §3.
   - `_parse_and_validate(raw: str) -> AlertSummary` — see §4.
   - `_scoped_evidence(anomaly: Anomaly) -> dict[str, Any]` — extracts only
     the whitelisted fields for prompt assembly (§3.2).

---

## 3. Prompt Design

### 3.1 Privacy boundary (from `docs/architecture.md`)

Allowed in prompt: anomaly `type`, `severity`, `tenant_id`, `subject`,
`observed`, `baseline`, `ratio`, `evidence` (already a summary), and
`recommended_actions`.

Disallowed: full `Event` objects, raw payloads, headers, message bodies.
The `Anomaly` dataclass already enforces this boundary — we just must not
re-introduce raw logs.

### 3.2 `_scoped_evidence` whitelist

Build a `dict` with exactly:
- `type`, `severity`, `tenant_id`, `subject`, `observed`, `baseline`,
  `ratio`, `evidence`, `recommended_actions`.

Explicitly exclude: `id`, `window_start`, `window_end` (irrelevant for
summarization; window timestamps included as a separate human-readable
field in the prompt body, not in the JSON blob).

### 3.3 Prompt template (string composition)

```
You are an SRE assistant. Summarize the anomaly below for a Slack alert.
Respond with ONLY a JSON object containing exactly these keys:
title, severity, summary, likely_cause, recommended_action, confidence.

Rules:
- severity must be one of: low, medium, high, critical.
- confidence must be one of: low, medium, high.
- Keep summary under 240 characters.
- Do not invent facts beyond the evidence shown.

Anomaly evidence (scoped — no raw log payloads):
<json blob of _scoped_evidence(anomaly)>
```

Key constraints validated by the existing `StubClient`:
- The prompt body must contain the literal substring of a request id from
  `evidence.sample_request_ids` (the test seeds `"req_1"`). The
  `json.dumps(scoped, ...)` rendering of `evidence` guarantees this.
- The prompt body must NOT contain the literal substring `raw full logs`
  (case-insensitive). The phrasing `"no raw log payloads"` ensures the
  literal substring `"raw full logs"` never appears in the prompt body.

---

## 4. Output Validation

`_parse_and_validate(raw: str) -> AlertSummary` performs, in order:

1. **JSON parse**: `json.loads(raw)` — `JSONDecodeError` → `ValueError`.
2. **Type check**: must be a `dict`, else `ValueError`.
3. **Required-fields check**: each name in `_REQUIRED_FIELDS` must be
   present and be a non-empty `str`, else `ValueError`.
4. **Enum checks**:
   - `severity` in `_VALID_SEVERITIES`
   - `confidence` in `_VALID_CONFIDENCES`
   - else `ValueError`.
5. **Length guard**: `len(summary) <= 480` (soft cap — generous but
   prevents pathological outputs), else `ValueError`.
6. Construct and return `AlertSummary(**only_required_fields)`.

`Summarizer.summarize` catches `ValueError` (validation) and `Exception`
(network/HTTP/timeout) and delegates to `FallbackSummarizer`.
Network/timeout/HTTP errors caught: `urllib.error.URLError`,
`urllib.error.HTTPError`, `TimeoutError`, `socket.timeout`, plus
`json.JSONDecodeError` from the Ollama envelope parse. We catch the broad
`Exception` super-class explicitly so any future client (e.g. httpx-based)
also fails safe — and we log the exception class name at WARNING.

---

## 5. `FallbackSummarizer` behavior (mapped to existing tests)

| Field                | Source                                                    | Test assertion                                                 |
|----------------------|-----------------------------------------------------------|----------------------------------------------------------------|
| `title`              | `f"{anomaly.severity.capitalize()} {anomaly.type} detected"` | `summary.title == "High sms_abuse_spike detected"`            |
| `severity`           | `anomaly.severity` (validated)                            | (implicit — same as anomaly)                                   |
| `summary`            | `f"Tenant {tenant_id} triggered {type} between {ws} and {we}."` | `"tenant_acme" in summary.summary`                       |
| `likely_cause`       | `anomaly.evidence["notes"][0]` (defensive default)        | (no direct assertion — must be a non-empty str)                |
| `recommended_action` | `anomaly.recommended_actions[0]`                          | `first.recommended_action == "Temporarily throttle the API key"` |
| `confidence`         | literal `"low"`                                           | `summary.confidence == "low"`                                  |

Determinism check: `first == second` for two `FallbackSummarizer()`
instances summarizing the same `Anomaly`. Achieved because:
- `AlertSummary` is `frozen=True` → `__eq__` is field-wise.
- All field derivations are pure functions of `Anomaly` (no clocks, RNG,
  or container ordering surprises).

---

## 6. Config Additions

### 6.1 Changes to `src/opsmitra/config.py` (`ModelConfig`)

Current `ModelConfig`:
```text
provider: str
endpoint_url: str | None
model_name: str | None
```

Updated `ModelConfig`:
```text
provider: str
endpoint_url: str           # default "http://localhost:11434"
model_name: str             # default "llama3.1:8b"
timeout_seconds: float      # default 10.0
```

Rationale: per acceptance criteria, model identity must be **pinned** in
config — so `model_name` and `endpoint_url` become required non-None
strings with sensible defaults, not `Optional`. This matches the
"no hardcoded model names in code" rule: the default lives in `config.py`
exactly once.

### 6.2 Environment variables

| Env var                      | Default                  | Field             |
|------------------------------|--------------------------|-------------------|
| `OPSMITRA_MODEL_PROVIDER`    | `fallback`               | `provider`        |
| `OPSMITRA_MODEL_URL`         | `http://localhost:11434` | `endpoint_url`    |
| `OPSMITRA_MODEL_NAME`        | `llama3.1:8b`            | `model_name`      |
| `OPSMITRA_MODEL_TIMEOUT`     | `10`                     | `timeout_seconds` |

**Backward-compat note**: the existing `OPSMITRA_MODEL_ENDPOINT_URL` env
var is renamed to `OPSMITRA_MODEL_URL` per the build plan's explicit
spelling. The old name is not yet used anywhere in code, so this is a
safe rename. If any docs reference the old name, they will be updated in
Step 9 (`update-docs`).

### 6.3 Parsing

Add helper `_parse_float(value: str, *, default: float) -> float` mirroring
`_parse_bool`. On `ValueError`, return `default` (defensive, no crash on
malformed env input).

---

## 7. Test Additions (beyond existing 3)

All tests live in `tests/test_summarizer.py` (extend) and
`tests/test_config.py` (extend; create if missing).

### 7.1 `tests/test_summarizer.py` additions

1. **`test_ollama_client_falls_back_on_http_error`**
   - Patch `urllib.request.urlopen` with `unittest.mock.patch` to raise
     `urllib.error.HTTPError(url, 500, "Server Error", {}, None)`.
   - Build `Summarizer(OllamaClient("test-model", "http://localhost:11434",
     timeout_seconds=1.0))` and call `summarize(_anomaly())`.
   - Assert result is the fallback (`title == "High sms_abuse_spike
     detected"`, `confidence == "low"`).

2. **`test_ollama_client_falls_back_on_timeout`**
   - Patch `urlopen` to raise `TimeoutError("simulated")`.
   - Assert fallback path is taken.

3. **`test_ollama_client_falls_back_on_malformed_envelope`**
   - Patch `urlopen` to return a fake response whose `.read()` returns
     `b'not json'`.
   - Assert fallback path.

4. **`test_summarizer_rejects_invalid_severity`**
   - Stub client returns valid JSON but with `"severity": "extreme"`.
   - Assert fallback path (since `"extreme"` is not in
     `_VALID_SEVERITIES`).

5. **`test_summarizer_rejects_missing_field`**
   - Stub client returns JSON missing `likely_cause`.
   - Assert fallback path.

6. **`test_prompt_excludes_raw_logs_phrase_and_includes_evidence`**
   - Use a recording client that captures the prompt; assert
     `"raw full logs" not in prompt.lower()` and
     `"req_1" in prompt` (mirrors the inline assertions but as an
     explicit, named test).

### 7.2 `tests/test_config.py` additions

1. **`test_model_config_defaults`**
   - `load_config(env={})` → `cfg.model.model_name == "llama3.1:8b"`,
     `cfg.model.endpoint_url == "http://localhost:11434"`,
     `cfg.model.timeout_seconds == 10.0`.

2. **`test_model_config_env_overrides`**
   - `load_config(env={"OPSMITRA_MODEL_NAME": "mistral:7b",
     "OPSMITRA_MODEL_URL": "http://10.0.0.5:11434",
     "OPSMITRA_MODEL_TIMEOUT": "3.5"})` → fields match overrides.

3. **`test_model_config_timeout_malformed_falls_back_to_default`**
   - `load_config(env={"OPSMITRA_MODEL_TIMEOUT": "not-a-number"})` →
     `cfg.model.timeout_seconds == 10.0`.

### 7.3 Coverage target

- Project trend: ≥95% (Step 4 baseline).
- All new branches (success path, JSON parse failure, missing fields,
  invalid enum, network error, timeout, malformed envelope, config
  malformed timeout) have a dedicated test → ≥95% on
  `src/opsmitra/summarizer.py` achievable.
- The narrow `tenant_id is None` defensive branch in `FallbackSummarizer`
  is exercised by an additional unit test
  (`test_fallback_handles_missing_tenant_id`).

---

## 8. Acceptance Criteria — 1:1 Mapping

| Build Plan Step 5 criterion                                                                                              | Plan section that satisfies it                                       |
|---------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| "Summarizer works with a mocked model client in tests."                                                                  | §7.1 (StubClient, BrokenClient, recording client, HTTP-error mocks). |
| "Prompt includes scoped evidence only, not raw full logs."                                                               | §3.2 whitelist + §3.3 final phrasing + §7.1 prompt test.             |
| "Model output has a validated fallback if the local model is unavailable."                                               | §4 validation + §5 FallbackSummarizer + §7.1 fallback tests.         |
| "Model identity is pinned in config: model name (e.g. `llama3.1:8b`)."                                                    | §6.1 `model_name: str` with default + §7.2 default test.             |
| "...endpoint URL (`OPSMITRA_MODEL_URL`, default `http://localhost:11434`)..."                                            | §6.2 env table + §7.2 default test.                                  |
| "...and a hard request timeout that triggers the fallback path."                                                          | §2.5 timeout wiring + §4 timeout catch + §7.1 timeout test.          |
| "No hardcoded model names in code."                                                                                       | §2.2 constants rule (no `"llama3.1:8b"` in `summarizer.py`).         |

---

## 9. Phases & Sequencing

Sequenced for the project's TDD workflow (PLAN → TDD → BUILD-FIX → VERIFY → REVIEW).

**Phase A — Config plumbing (TDD agent)**
- Write tests in §7.2 first (RED).
- Update `ModelConfig`, `load_config`, add `_parse_float`.
- Verify tests pass (GREEN).

**Phase B — `FallbackSummarizer` + `AlertSummary` (TDD agent)**
- The existing `test_fallback_summarizer_is_deterministic` already
  serves as RED.
- Add `test_fallback_handles_missing_tenant_id` (RED).
- Implement `AlertSummary` + `FallbackSummarizer`.
- Verify tests pass (GREEN).

**Phase C — `ModelClient` Protocol + `Summarizer` JSON validation
(TDD agent)**
- Existing `test_summarizer_validates_model_json_output` and
  `test_summarizer_falls_back_when_model_output_is_invalid` are RED.
- Add validation tests §7.1.4 / §7.1.5 / §7.1.6 (RED).
- Implement `ModelClient`, `Summarizer`, `_build_prompt`,
  `_parse_and_validate`.
- Verify tests pass (GREEN).

**Phase D — `OllamaClient` + HTTP error paths (TDD agent)**
- Add tests §7.1.1, §7.1.2, §7.1.3 (RED).
- Implement `OllamaClient` with `urllib`.
- Verify tests pass (GREEN).

**Phase E — Verification (verify agent)**
- Full `pytest --cov` run; assert coverage ≥95%.
- `ruff` + `black` checks.

**Phase F — Review (python-review agent)**
- Confirm no hardcoded model names.
- Confirm privacy boundary (no `Event` import in `summarizer.py`).
- Confirm immutability (frozen dataclass, no mutation).

---

## 10. Dependencies

- **stdlib only** — no new third-party deps.
  - `urllib.request`, `urllib.error`, `json`, `socket`, `logging`,
    `dataclasses`, `typing`.
- No changes to `pyproject.toml` runtime deps.
- Test-only: `unittest.mock` (stdlib) for patching `urlopen`. No need to
  add `responses` or `httpx-mock`.

---

## 11. Risks & Open Questions

### 11.1 urllib vs httpx (decision: **urllib**)

**Decision**: use stdlib `urllib.request`.

**Rationale**:
- Zero new dependencies (project is currently stdlib + pytest only).
- The OpsMitra `urllib` blast radius is small: one `POST` to one local
  URL, JSON in / JSON out, hard timeout. We don't need connection pooling,
  HTTP/2, or async.
- `urllib` is trivially mockable with `unittest.mock.patch(
  "urllib.request.urlopen", ...)` — keeps the test surface stdlib-only.

**Cost**: clunkier API; manual `Request` construction; no built-in
retries. We accept this since retries belong at the runtime/scheduler
layer (Step 8), not inside the model client.

**Open question for the user**: confirm urllib is acceptable, or whether
to add `httpx` as a foundational dep now (cheaper to add early than
mid-project).

### 11.2 Ollama `format: json` mode vs plain prompt

**Decision**: pass `"format": "json"` in the Ollama request body, plus
explicit prompt-level JSON instructions.

**Rationale**:
- Ollama's `format: json` is a constrained-decoding hint that
  significantly reduces malformed-JSON output for capable models.
- Belt-and-suspenders: prompt instructions still tell the model exactly
  which keys to emit (constrained decoding doesn't enforce schema).
- Validation in `_parse_and_validate` is the actual safety net.

**Open question for the user**: confirm `format: json` is fine, or
whether to use the `/api/chat` endpoint with structured outputs
instead. The `/api/generate` + `format: json` combo is simpler and
universally supported across Ollama versions.

### 11.3 Backward-compat of env var rename

`OPSMITRA_MODEL_ENDPOINT_URL` (current) → `OPSMITRA_MODEL_URL` (build
plan spelling). Not yet used by any caller, so renaming is safe **now**.
Risk: any out-of-tree script setting the old var silently uses the
default. Mitigation: call this out in `update-docs` (Step 9).

**Open question for the user**: confirm the rename is OK, or keep both
names with the new one taking precedence.

### 11.4 Defensive validation strictness

The validator rejects empty strings and unknown enum values. It does
**not** reject extra keys (e.g., the model adds `"explanation"`). We
silently drop them. Stricter alternative: reject any unknown key →
fallback. Risk of strict mode: brittle against minor prompt drift.

**Decision (pending user confirm)**: lenient on extra keys, strict on
missing/invalid.

### 11.5 Logging of prompt content

We log `WARNING` on fallback paths with anomaly id and exception class
only — **never** the prompt body or model response — to avoid leaking
tenant identifiers into log aggregators. Aligned with privacy boundary
in `architecture.md`.

---

## 12. Out of Scope (for this step)

- Slack delivery (Step 6).
- Per-tenant model selection (Step 7).
- Scheduler / cooldown / fingerprinting (Step 8).
- Replay-based evaluation of summary quality (Step 9).
- Provider auto-detection or multi-provider routing.
- Streaming responses (we hard-set `"stream": false`).
- Retries (caller/scheduler's responsibility).

---

## 13. Complexity Estimate

- New file `summarizer.py`: ~180 lines.
- `config.py` delta: ~25 lines.
- New tests: ~140 lines.
- Total estimated implementation time after plan approval: 2–3 hours.
- Complexity: **MEDIUM** — clear contract from existing tests, stdlib
  only, but several distinct failure paths to cover for ≥95% coverage.

---

## 14. Ready-for-TDD Checklist

- [x] Public contract pinned by existing tests — re-read.
- [x] Privacy boundary explicit (`_scoped_evidence` whitelist).
- [x] Prompt phrasing avoids the literal `"raw full logs"` substring.
- [x] All env vars and defaults enumerated.
- [x] Fallback field-by-field mapping documented.
- [x] Failure paths enumerated (JSON parse, missing field, invalid
      enum, HTTP error, timeout, malformed envelope, malformed env var).
- [x] Coverage target stated and per-branch tests planned.
- [x] Open questions surfaced for user confirmation.
- [ ] **User confirmation** to proceed to TDD phase.

**WAITING FOR CONFIRMATION**: Proceed to Step 2 (TDD) with this plan?
(yes / no / modify)
