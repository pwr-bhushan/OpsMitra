# Step 6: Add Slack Alert Delivery — Implementation Plan

**Build Plan reference**: `.claude/plans/opsmitra-build-plan.md` → Step 6
**Branch**: `dev` (mandatory per `lessons.md`)
**Workflow phase**: PLAN (no code in this phase)
**Predecessor**: Step 5 (`AlertSummary` produced by `opsmitra.summarizer.Summarizer`)
**Successor**: Step 8 (Scheduler/Runtime — owns fingerprint cooldown, retry orchestration above the alert layer)

---

## 1. Requirements Restatement

Implement a Slack incoming-webhook delivery layer that turns an `AlertSummary`
(from Step 5) plus the originating `Anomaly` into a Block Kit message and POSTs
it to a Slack incoming webhook. The layer must:

1. **Read the webhook URL only from env/config** — never hardcoded, never
   accepted as a function argument from random callers other than `AlertConfig`.
2. **Support dry-run mode (default `true` for local dev)** — in dry-run, the
   final JSON payload is returned/logged but no network call is made.
3. **Format messages with a pure function** — `format_alert(summary, anomaly) ->
   dict` is unit-testable in isolation, no I/O, no network, no clock.
4. **Retry on transient failures only** — bounded exponential backoff for 5xx,
   `URLError`, `TimeoutError`; never retry on 4xx (caller misconfig); hard cap
   on total wait time.
5. **Never leak the webhook URL** — redact it from every log line and every
   exception message that escapes the module. Replace with the literal token
   `[REDACTED_WEBHOOK_URL]`.
6. **Decouple from Step 8** — fingerprinting / cooldown / dedup belongs to the
   scheduler. This module is fire-once, idempotent only in the "Slack accepts
   duplicates" sense.

Decomposed testable subgoals:

| Subgoal                                                        | Verified by                                              |
|----------------------------------------------------------------|----------------------------------------------------------|
| Pure formatting yields stable Block Kit shape                  | `test_format_alert_block_kit_structure_per_severity`     |
| Severity-specific emoji + color                                | `test_format_alert_emoji_per_severity` (parametrized)    |
| Dry-run never calls the network                                | `test_send_dry_run_returns_payload_no_network`           |
| Missing webhook in non-dry-run raises a clear error            | `test_send_missing_webhook_raises_configuration_error`   |
| Successful send returns a success result                       | `test_send_success_returns_ok_result`                    |
| Retryable 5xx triggers retry, eventually succeeds              | `test_send_retries_on_5xx_then_succeeds`                 |
| Non-retryable 4xx fails immediately                            | `test_send_does_not_retry_on_4xx`                        |
| Timeout triggers retry, then gives up after cap                | `test_send_retries_on_timeout_then_gives_up`             |
| Webhook URL never appears in raised exceptions or log messages | `test_webhook_url_never_appears_in_logs_or_exceptions`   |
| Top-N evidence bullets truncated, never includes raw logs      | `test_format_alert_evidence_bullets_truncated`           |

---

## 2. File Layout & Module Responsibilities

### 2.1 New file: `src/opsmitra/slack_alerter.py`

Single new module, ~220 lines. Top-to-bottom layout:

1. **Module docstring + imports**
   - `from __future__ import annotations`
   - stdlib only: `json`, `logging`, `time`, `dataclasses`, `typing`
     (`Callable`, `Protocol`), `urllib.request`, `urllib.error`.
   - `from opsmitra.config import AlertConfig`
   - `from opsmitra.models import Anomaly`
   - `from opsmitra.summarizer import AlertSummary`
   - `logger = logging.getLogger(__name__)`

2. **Constants (module-level, no hardcoded URLs or secrets)**
   - `_REDACTED = "[REDACTED_WEBHOOK_URL]"`
   - `_SEVERITY_EMOJI = {"low": ":information_source:", "medium": ":warning:",
     "high": ":rotating_light:", "critical": ":fire:"}`
   - `_SEVERITY_COLOR = {"low": "#36a64f", "medium": "#daa038",
     "high": "#d93f0b", "critical": "#b30000"}` (used in optional attachment)
   - `_MAX_EVIDENCE_BULLETS = 5`
   - `_RETRYABLE_STATUS_PREFIX = 5`   # any 5xx
   - `_DEFAULT_USER_AGENT = "opsmitra-slack-alerter/1.0"`

3. **Result + error types (immutable)**
   ```text
   @dataclass(frozen=True)
   class DeliveryResult:
       delivered: bool          # True = sent (or dry-run); False = error
       dry_run: bool
       attempts: int
       status_code: int | None  # None for dry-run or pre-network failure
       payload: dict            # the Block Kit dict that was (or would be) sent

   class SlackConfigurationError(ValueError):
       """Raised when AlertConfig is invalid for sending (e.g. missing URL
       outside dry-run)."""
   ```
   `DeliveryResult` is `frozen=True` (immutability rule). Equality is field-wise
   so tests can assert exact results.

4. **Pure formatting helper**
   ```text
   def format_alert(summary: AlertSummary, anomaly: Anomaly) -> dict:
       """Return the Block Kit JSON payload for the given alert.
       Pure function: no I/O, no clock, no RNG. See §5 for the shape."""
   ```

5. **`SlackAlerter` class (public API)**
   ```text
   class SlackAlerter:
       def __init__(
           self,
           config: AlertConfig,
           *,
           sleep: Callable[[float], None] = time.sleep,
           opener: Callable[..., Any] | None = None,
       ) -> None: ...

       def send(self, summary: AlertSummary, anomaly: Anomaly) -> DeliveryResult:
           ...
   ```
   - `sleep` is injectable so retry tests can use a `MagicMock` and avoid real
     waits.
   - `opener` is the urllib opener; when `None` we use module-level
     `urllib.request.urlopen` (so tests `patch("opsmitra.slack_alerter.urlopen",
     ...)` exactly like `test_summarizer.py` does).

6. **Private helpers**
   - `_post_with_retry(self, payload: dict) -> tuple[int, int]` →
     `(status_code, attempts)`. Encapsulates the retry loop.
   - `_should_retry(exc_or_status: int | Exception) -> bool` — single decision
     point.
   - `_backoff_seconds(attempt: int) -> float` — pure: `base * 2 ** (attempt-1)`
     capped at `max_backoff_seconds`.
   - `_redact(text: str) -> str` — replaces the configured webhook URL with
     `_REDACTED` anywhere in the input string. Used to scrub exception messages
     before logging or re-raising.
   - `_safe_log_error(self, message: str, *args: Any) -> None` — convenience
     wrapper that redacts before calling `logger.error`.

### 2.2 Modified file: `src/opsmitra/config.py`

Extend `AlertConfig` only — no new top-level dataclass. See §4.

### 2.3 New test file: `tests/test_slack_alerter.py`

~250 lines, ~12 tests. Mirrors `tests/test_summarizer.py` mocking idioms
(`MagicMock` for the urlopen context manager with `__enter__`/`__exit__`).

### 2.4 Modified test file: `tests/test_config.py`

Add 3 short tests for new env vars (defaults, overrides, malformed retry int).

### 2.5 Public API decision: **`SlackAlerter` class + `format_alert` function**

**Choice**: ship both:
- `format_alert(summary, anomaly) -> dict` — pure, importable directly, used
  for assertions and by the dry-run path.
- `SlackAlerter(config).send(summary, anomaly) -> DeliveryResult` — stateful
  shell that owns retry state and the URL → redaction binding.

**Justification**:
- A class makes injecting `sleep` and `opener` (for retry/network tests) clean
  without mutating function signatures or smuggling kwargs through helpers.
- A separate pure `format_alert` is the formal seam between formatting and
  delivery, and it's the function the acceptance test "alert formatting is
  tested without sending real messages" directly exercises.
- Keeps the surface symmetric with `summarizer.py` (`Summarizer` class +
  `_build_prompt` private helper), but `format_alert` is *public* because Step
  8's runtime will call it directly when logging a dry-run preview.

---

## 3. Acceptance Criteria — 1:1 Mapping

| Build Plan Step 6 criterion                                          | Plan section that satisfies it                              |
|----------------------------------------------------------------------|-------------------------------------------------------------|
| "Alert formatting is tested without sending real messages."          | §5 Block Kit contract + §8.1 formatting tests (no network). |
| "Webhook URL is read from environment/config only."                  | §4 `AlertConfig` + §7 secret-handling rules.                |
| "Dry-run mode prints the final alert payload."                       | §6 dry-run path + §8.1 `test_send_dry_run_returns_payload`. |

Plus user-supplied derived criteria:

| Derived criterion                                            | Plan section                                  |
|--------------------------------------------------------------|-----------------------------------------------|
| Retry on transient errors only; no retry on 4xx              | §6 retry policy table + §8.1 retry tests.     |
| Webhook URL never appears in logs/exceptions                 | §7 redaction rules + §8.1 redaction test.     |
| ≥90% line coverage on `slack_alerter.py`                    | §8.3 coverage target + branch enumeration.    |
| Teams support is a future extension point                    | §2.1 (no Teams code; §11 open question).      |

---

## 4. Config Additions

### 4.1 Changes to `src/opsmitra/config.py` (`AlertConfig`)

Current `AlertConfig`:
```text
slack_webhook_url: str | None
dry_run: bool
```

Updated `AlertConfig`:
```text
slack_webhook_url: str | None         # unchanged
dry_run: bool                          # unchanged (default True)
slack_timeout_seconds: float           # NEW, default 5.0
slack_max_retries: int                 # NEW, default 3 (≥ 0)
slack_backoff_base_seconds: float      # NEW, default 0.5
slack_backoff_max_seconds: float       # NEW, default 8.0
slack_total_wait_cap_seconds: float    # NEW, default 15.0
slack_channel_override: str | None     # NEW, optional; incoming webhooks
                                       # ignore this unless explicitly set, but
                                       # we forward it as "channel" in the body
                                       # if present (Slack legacy compat).
```

### 4.2 Environment variables

| Env var                                  | Default                  | Field                          | Validation                |
|------------------------------------------|--------------------------|--------------------------------|---------------------------|
| `OPSMITRA_SLACK_WEBHOOK_URL`            | unset → `None`           | `slack_webhook_url`            | string; if set, must start with `https://hooks.slack.com/` (warn-only) |
| `OPSMITRA_DRY_RUN`                       | `true`                   | `dry_run`                      | bool parse, default `True` (unchanged) |
| `OPSMITRA_SLACK_TIMEOUT_SECONDS`        | `5`                      | `slack_timeout_seconds`        | float > 0; `ValueError` on garbage |
| `OPSMITRA_SLACK_MAX_RETRIES`            | `3`                      | `slack_max_retries`            | int ≥ 0; `ValueError` on garbage |
| `OPSMITRA_SLACK_BACKOFF_BASE_SECONDS`   | `0.5`                    | `slack_backoff_base_seconds`   | float ≥ 0 |
| `OPSMITRA_SLACK_BACKOFF_MAX_SECONDS`    | `8`                      | `slack_backoff_max_seconds`    | float ≥ `base` |
| `OPSMITRA_SLACK_TOTAL_WAIT_CAP_SECONDS` | `15`                     | `slack_total_wait_cap_seconds` | float ≥ 0 |
| `OPSMITRA_SLACK_CHANNEL_OVERRIDE`       | unset → `None`           | `slack_channel_override`       | optional string |

### 4.3 Parsing strategy

- Reuse existing `_parse_float` and `_parse_bool`; add `_parse_int(value: str)
  -> int` mirroring `_parse_float` (raises `ValueError` on bad input per
  lessons.md: "Malformed config inputs should raise `ValueError` to surface
  misconfiguration loudly").
- Webhook URL **shape validation**: log a `WARNING` (no exception) if set but
  does not start with `https://hooks.slack.com/`. The URL value itself is
  never logged — only "OPSMITRA_SLACK_WEBHOOK_URL has unexpected prefix"
  marker. Refusing to start would block Slack Enterprise Grid alt domains.

### 4.4 Rationale

- Defaults are tuned for a local 3am on-call: total wait cap 15s keeps the
  scheduler responsive; 3 retries with 0.5/1/2s backoff covers a typical
  transient blip.
- All numeric knobs are config so the Step 8 scheduler can tighten them for
  Lambda's 15-minute limit.

---

## 5. Message Formatting Contract (Block Kit)

### 5.1 Top-level payload shape

```text
{
    "blocks": [<header>, <context>, <evidence>, <action>],
    "attachments": [
        {
            "color": <severity color>,
            "fallback": <title plain text>,
        }
    ],
    "text": <title plain text>,           # fallback for notifications
    # "channel": <override>,              # only if slack_channel_override set
}
```

### 5.2 Per-block specification

#### Header block
```text
{
    "type": "header",
    "text": {
        "type": "plain_text",
        "text": f"{emoji} {summary.title}",
        "emoji": true,
    },
}
```
- `emoji` is selected from `_SEVERITY_EMOJI[summary.severity]` (severity is
  validated to be in `{low, medium, high, critical}` upstream).

#### Context block (tenant / type / confidence)
```text
{
    "type": "context",
    "elements": [
        {"type": "mrkdwn", "text": f"*Tenant:* `{tenant_id}`"},
        {"type": "mrkdwn", "text": f"*Type:* `{anomaly.type}`"},
        {"type": "mrkdwn", "text": f"*Confidence:* `{summary.confidence}`"},
        {"type": "mrkdwn", "text": f"*Window:* `{window_start_iso} → {window_end_iso}`"},
    ],
}
```
- `tenant_id` defaults to `"unknown"` if `anomaly.tenant_id is None`.
- All values wrapped in backticks for code formatting and to defang any
  accidental Markdown injection from the model output.

#### Evidence section block
```text
{
    "type": "section",
    "text": {
        "type": "mrkdwn",
        "text": "*Summary*\n" + summary.summary + "\n\n*Evidence*\n" +
                "\n".join(f"• {b}" for b in evidence_bullets),
    },
}
```
- `evidence_bullets` are derived from `anomaly.evidence` via `_evidence_bullets`
  helper:
  - Start with `anomaly.evidence.get("notes") or []`.
  - Append `f"observed: {anomaly.observed}"` if `observed` non-empty.
  - Append `f"baseline: {anomaly.baseline}"` if present.
  - Append `f"ratio: {anomaly.ratio:.2f}x"` if `ratio is not None`.
  - Cap at `_MAX_EVIDENCE_BULLETS` (5). Drop overflow silently.
  - Each bullet is `str()`-coerced and **never** includes raw `Event` objects.
- `summary.summary` is passed through with no transformation — it's already
  validated ≤480 chars by Step 5.

#### Action footer block
```text
{
    "type": "section",
    "text": {
        "type": "mrkdwn",
        "text": f"*Suggested action:* {summary.recommended_action}\n"
                f"_Likely cause:_ {summary.likely_cause}",
    },
}
```

### 5.3 Per-severity examples (abbreviated; full payload in test fixtures)

| Severity   | Header emoji         | Attachment color | Use case                       |
|------------|----------------------|------------------|--------------------------------|
| `low`      | `:information_source:` | `#36a64f`      | informational, no page         |
| `medium`   | `:warning:`          | `#daa038`        | review next business day        |
| `high`     | `:rotating_light:`   | `#d93f0b`        | page on-call now                |
| `critical` | `:fire:`             | `#b30000`        | wake the on-call up             |

### 5.4 Stability guarantees (asserted by tests)

- Dict key order is preserved by `json.dumps` in Python 3.7+; tests assert by
  structure not string equality.
- `format_alert` is pure: two calls with the same inputs produce equal dicts.
- The payload **never** contains:
  - The webhook URL.
  - Raw `Event` objects (we only ever read `Anomaly` fields).
  - Any field outside the `Anomaly` / `AlertSummary` whitelist.

---

## 6. Retry Policy

### 6.1 Decision table

| Outcome                                           | Retry? | Notes                                       |
|---------------------------------------------------|--------|---------------------------------------------|
| 2xx response                                      | No     | Success.                                    |
| 3xx response                                      | No     | Treat as configuration error (no follow).   |
| 4xx response (400, 403, 404, 410, ...)            | **No** | Caller misconfig; retry won't help.         |
| 429 Too Many Requests                             | **No** | Step 8 cooldown handles dedup; we don't honor `Retry-After` in v0. Documented as open question §11.4. |
| 5xx response                                      | Yes    | Transient server error.                     |
| `urllib.error.URLError` (DNS, connection refused) | Yes    | Transient network.                          |
| `TimeoutError` / `socket.timeout`                 | Yes    | Transient.                                  |
| `urllib.error.HTTPError` with 5xx                 | Yes    | Same as 5xx above.                          |
| `urllib.error.HTTPError` with 4xx                 | No     | Caller misconfig.                           |
| Any other `Exception`                             | No     | Unknown; log and surface as failure result. |

### 6.2 Backoff schedule

- `backoff_seconds(attempt) = min(base * 2 ** (attempt - 1), max_backoff)`
- With defaults (`base=0.5`, `max=8`): waits before attempts 2, 3, 4 are
  `0.5s`, `1.0s`, `2.0s`. Total worst case = `3.5s` < cap `15s` → cap rarely
  triggers at defaults; it's a guardrail for misconfiguration.
- Max attempts = `slack_max_retries + 1` (i.e. 1 initial + `slack_max_retries`
  retries). Default total attempts = **4**.
- Total elapsed wait must not exceed `slack_total_wait_cap_seconds`. If the
  next backoff would push past the cap, stop retrying and return a failed
  `DeliveryResult` immediately.

### 6.3 Sleep injection

`SlackAlerter` accepts `sleep: Callable[[float], None] = time.sleep`. Retry
tests pass a `MagicMock` so no wall-clock waits run in CI. The mock also
provides the assertion vehicle for backoff scheduling
(`mock_sleep.call_args_list == [call(0.5), call(1.0), call(2.0)]`).

---

## 7. Secret-Handling Rules

### 7.1 Where the URL lives

- **Only** in `AlertConfig.slack_webhook_url`, loaded from
  `OPSMITRA_SLACK_WEBHOOK_URL`.
- `SlackAlerter.__init__` stores it on `self._webhook_url`.
- The URL is passed to `urllib.request.Request(url=...)` **once** per attempt
  and never copied into other variables.

### 7.2 What we redact

`SlackAlerter._redact(text)` replaces every occurrence of `self._webhook_url`
in a string with `_REDACTED`. Called before:

1. Every `logger.error` / `logger.warning` invocation in this module.
2. Every exception message that escapes the module (re-raised with a scrubbed
   message via `raise type(e)(self._redact(str(e))) from None` — `from None`
   suppresses the chained traceback that would otherwise contain the original
   exception with the URL).

### 7.3 What we log

| Event                    | Level    | Content                                                       |
|--------------------------|----------|---------------------------------------------------------------|
| Dry-run preview          | INFO     | `"dry-run: would send Slack alert (severity=%s, type=%s)"`  + `json.dumps(payload)` (payload has no secrets). |
| Send success             | INFO     | `"Slack alert delivered (severity=%s, attempts=%d)"` — no URL.       |
| Retry attempt            | WARNING  | `"Slack send transient failure (attempt=%d/%d, reason=%s); backing off %.2fs"` — `reason` is exception class name only. |
| Final failure            | ERROR    | `"Slack send failed after %d attempts (reason=%s)"` — `reason` is the redacted exception message or status code. |
| Configuration error      | ERROR    | `"Slack send aborted: webhook URL not configured"` — never prints any URL fragment. |

### 7.4 What we never log

- The webhook URL, in full or in part (no last-4 fingerprint, no domain).
- The `Authorization` header (none used; Slack incoming webhooks are
  bearer-tokens-in-URL — extra reason to redact).
- The raw `urllib` exception's `.url` attribute (for `HTTPError`, this is the
  full URL — we explicitly redact it).
- The payload in success logs (it's safe but verbose; dry-run is the only
  payload-printing path).

### 7.5 Negative test

`test_webhook_url_never_appears_in_logs_or_exceptions` (§8.1) configures a
URL containing a unique sentinel substring (e.g.
`"https://hooks.slack.com/services/UNIQUE-SENTINEL"`), forces every failure
path (HTTPError 500, URLError, TimeoutError, missing-URL config error), captures
both `caplog` records and any raised exceptions, and asserts the sentinel
substring appears in **zero** of those captures.

---

## 8. Test Plan

All tests live in `tests/test_slack_alerter.py` (new) and `tests/test_config.py`
(extend). Mocking idioms mirror `tests/test_summarizer.py` exactly:
`MagicMock` for the urllib response with `__enter__`/`__exit__` plumbing.

### 8.1 `tests/test_slack_alerter.py` (12 tests)

| # | Test                                                              | What it asserts                                                                                                                                  |
|---|-------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| 1 | `test_format_alert_block_kit_structure`                          | Returned dict has top-level `blocks` (list of 4), `attachments` (list of 1), `text` (str). Block types are `header, context, section, section` in order. |
| 2 | `test_format_alert_emoji_per_severity` (parametrized 4 cases)    | Header text starts with the correct emoji for `low`/`medium`/`high`/`critical`.                                                                  |
| 3 | `test_format_alert_evidence_bullets_truncated`                   | When evidence notes contain > 5 items, only 5 bullets appear in the section text; raw `Event` data never appears.                                |
| 4 | `test_format_alert_omits_channel_when_no_override`               | No `channel` key when `slack_channel_override is None`; key present when set.                                                                    |
| 5 | `test_format_alert_is_pure_deterministic`                        | Two calls with the same `(summary, anomaly)` return equal dicts; no clock / RNG.                                                                |
| 6 | `test_send_dry_run_returns_payload_no_network`                   | With `dry_run=True`, `urlopen` is never called; `DeliveryResult.delivered=True, dry_run=True, payload=format_alert(...)`.                       |
| 7 | `test_send_missing_webhook_raises_configuration_error`           | With `dry_run=False, slack_webhook_url=None`, `send` raises `SlackConfigurationError`; message does not include any URL.                        |
| 8 | `test_send_success_returns_ok_result`                            | Fake `urlopen` returns 200 (status reachable via `getcode()` on the response mock); `DeliveryResult.delivered=True, attempts=1, status_code=200`. |
| 9 | `test_send_retries_on_5xx_then_succeeds`                         | `urlopen` raises `HTTPError(..., 502, ...)` twice then returns 200; `attempts==3`; `mock_sleep` called with `[0.5, 1.0]`.                       |
| 10| `test_send_does_not_retry_on_4xx`                                | `urlopen` raises `HTTPError(..., 403, ...)`; `attempts==1`; `mock_sleep` never called; `DeliveryResult.delivered=False, status_code=403`.       |
| 11| `test_send_retries_on_timeout_then_gives_up`                     | `urlopen` raises `TimeoutError` on every call; `attempts == max_retries+1`; final result `delivered=False, status_code=None`.                   |
| 12| `test_webhook_url_never_appears_in_logs_or_exceptions`           | Sentinel URL never appears in `caplog.text` or in `str(exc_info.value)` across configuration error, 4xx, 5xx-exhausted, URLError, TimeoutError paths. |

### 8.2 `tests/test_config.py` additions (3 tests)

| # | Test                                                            | What it asserts                                                                                  |
|---|-----------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| 1 | `test_alert_config_defaults`                                   | `cfg.alert.slack_timeout_seconds==5.0`, `slack_max_retries==3`, `slack_backoff_base_seconds==0.5`, `slack_backoff_max_seconds==8.0`, `slack_total_wait_cap_seconds==15.0`, `slack_channel_override is None`. |
| 2 | `test_alert_config_env_overrides`                              | All Slack env vars override their dataclass fields with correctly parsed types.                  |
| 3 | `test_alert_config_max_retries_malformed_raises_value_error`   | `OPSMITRA_SLACK_MAX_RETRIES="abc"` → `load_config(env=...)` raises `ValueError` (per lessons.md). |

### 8.3 Coverage target

- ≥90% line coverage on `src/opsmitra/slack_alerter.py` (acceptance target).
- Branch enumeration ensures coverage:
  - `format_alert` — bullet truncation branch, channel-override branch,
    None-tenant branch, missing-evidence-notes branch.
  - `send` — dry-run branch, missing-URL branch, success branch, 4xx branch,
    5xx-retry-then-success branch, timeout-exhaustion branch, total-wait-cap
    branch (open question: may be hard to exercise at defaults; use a tiny cap
    in a dedicated test).
- `_redact`, `_should_retry`, `_backoff_seconds` exercised indirectly through
  the retry tests; if branch coverage flags them as missed, add a 3-line direct
  unit test for `_backoff_seconds`.

### 8.4 Fixtures

- A single `_summary()` factory mirroring `test_summarizer.py::_anomaly()` to
  build a stable `AlertSummary`.
- A `_make_fake_response(body=b"ok", status=200)` helper modeled on
  `_make_fake_response` in `test_summarizer.py`, extended with a `status` arg
  and a `.getcode()` method (Slack returns `ok` as text but we read the HTTP
  status code).
- A `parametrize` block for severity tests.

---

## 9. Phased Implementation Order

Sequenced for the project's TDD workflow per `.claude/CLAUDE.md`
(PLAN → TESTS → IMPLEMENT → VERIFY → REVIEW → FIX-PLAN → FIX → RE-VERIFY).

### Phase A — Config plumbing (`tdd` then `build-fix`)
1. RED: Write §8.2 tests in `tests/test_config.py`.
2. GREEN: Extend `AlertConfig` per §4.1, env wiring per §4.2, add `_parse_int`
   helper per §4.3.
3. Verify: `pytest tests/test_config.py -xvs`.

### Phase B — Pure formatting (`tdd` then `build-fix`)
1. RED: Write §8.1 tests #1–#5 in `tests/test_slack_alerter.py`.
2. GREEN: Implement `format_alert`, `_evidence_bullets`, severity emoji/color
   constants in `src/opsmitra/slack_alerter.py`. **No** `send`, **no** network
   code yet.
3. Verify: `pytest tests/test_slack_alerter.py -xvs -k format`.

### Phase C — Dry-run and configuration error (`tdd` then `build-fix`)
1. RED: Write §8.1 tests #6, #7.
2. GREEN: Implement `SlackAlerter.__init__`, `send` dry-run branch,
   `SlackConfigurationError`, and the `DeliveryResult` dataclass.
3. Verify: `pytest tests/test_slack_alerter.py -xvs -k "dry_run or missing"`.

### Phase D — Network send + retry (`tdd` then `build-fix`)
1. RED: Write §8.1 tests #8, #9, #10, #11.
2. GREEN: Implement `_post_with_retry`, `_should_retry`, `_backoff_seconds`,
   and the network branch of `send`. Inject `sleep` via constructor.
3. Verify: `pytest tests/test_slack_alerter.py -xvs`.

### Phase E — Redaction (`tdd` then `build-fix`)
1. RED: Write §8.1 test #12.
2. GREEN: Implement `_redact`, wire it into every `logger.*` call and any
   exception re-raise in `send`.
3. Verify: `pytest tests/test_slack_alerter.py::test_webhook_url_never_appears_in_logs_or_exceptions -xvs`.

### Phase F — Verification (`verify` agent)
1. Full `pytest -xvs` run.
2. `pytest --cov=src/opsmitra --cov-report=term-missing`; assert
   `slack_alerter.py` ≥ 90%.
3. `ruff` + `black` checks.

### Phase G — Review (`python-review` agent)
1. Confirm no URL leaks in any code path.
2. Confirm `format_alert` is pure (no `time`, no `random`, no I/O imports
   referenced inside).
3. Confirm `urllib` mocking pattern matches `test_summarizer.py`.
4. Confirm `Anomaly` is the only Step-5 surface touched (no `Event` imports).
5. Confirm Teams extension point is plausible (next §10).

### Phase H — FIX-PLAN + FIX (if review surfaces issues)
1. Use `everything-claude-code:plan` to draft fix plan, write to
   `.claude/plans/step6-review-fixes.md`.
2. Apply via `build-fix` after user approval.

### Phase I — Documentation + Session save
1. `update-docs`: refresh `.claude/tasks/todo.md` Step 6 row to ✅; append to
   `lessons.md` if anything surprising surfaced.
2. `save-session`.

---

## 10. Teams (Future Extension Point)

This step is Slack-only. To prepare for Teams without over-engineering today:

- `format_alert` is named after the **abstract** alert, not Slack. A future
  `format_alert_teams(summary, anomaly) -> dict` can live alongside it.
- `SlackAlerter` is intentionally **not** a `Alerter` protocol today (YAGNI),
  but its public method is `.send(summary, anomaly)`, which is the obvious
  Protocol surface to extract in Step 10 / future-Teams work.
- `AlertConfig` keeps Slack-prefixed env vars (`OPSMITRA_SLACK_*`) so a future
  `OPSMITRA_TEAMS_*` set lives cleanly beside them.

No code is written for Teams in this step.

---

## 11. Risks & Open Questions

### 11.1 urllib vs httpx (decision: **urllib**)

**Decision**: stdlib `urllib.request`, consistent with `summarizer.py`.

**Rationale**: zero new deps; mockable with `unittest.mock.patch(
"opsmitra.slack_alerter.urlopen", ...)`; the entire surface is one POST.

**Cost**: manual `HTTPError` discrimination by `.code`; manual retry loop.
Accepted.

**Open question for user**: confirm urllib is OK, or pull in `httpx` now to
share retry primitives with future Teams + AWS API clients.

### 11.2 Honor Slack's `Retry-After` header on 429? (decision: **no, in v0**)

**Decision**: treat 429 as non-retryable in v0; rely on Step 8's fingerprint
cooldown to prevent spam in the first place.

**Rationale**: Slack rarely 429s incoming webhooks; Step 8 cooldown means we
won't be flooding; honoring `Retry-After` adds branching cost and clock
dependency in this layer.

**Open question for user**: confirm "429 = give up" is acceptable, or move
429 into the retry bucket and parse `Retry-After`.

### 11.3 Block Kit vs plain `text` payload (decision: **Block Kit + text fallback**)

**Decision**: send Block Kit blocks **and** a top-level `text` field for the
notification preview / accessibility fallback.

**Rationale**: Block Kit gives the on-call a scannable severity-colored
header at 3am; `text` ensures the mobile push notification preview is
useful even where Block Kit doesn't render.

**Open question for user**: confirm Block Kit is the right shape, or prefer
the older `attachments`-only style.

### 11.4 Webhook URL prefix validation strictness (decision: **warn-only**)

**Decision**: warn (no exception) if `OPSMITRA_SLACK_WEBHOOK_URL` doesn't
start with `https://hooks.slack.com/`.

**Rationale**: Enterprise Grid + relay proxies use alternate domains;
hard-failing would break legitimate setups.

**Open question for user**: confirm warn-only, or hard-fail at config load
to surface typos earlier.

### 11.5 Default `dry_run=true` in CI (decision: **yes**)

**Decision**: keep `OPSMITRA_DRY_RUN` default `true`. CI never sends real
Slack messages unless explicitly overridden.

**Rationale**: prevents accidental on-call pages from CI runs. Matches
"private, local-first" project ethos.

**No open question** — confirmed by Step 5 precedent.

### 11.6 `_redact` matches the configured URL exactly, not a regex pattern

**Risk**: a log line that contains a *partial* URL (e.g. the path with the
secret token but a different scheme) wouldn't be matched.

**Mitigation**: the only code paths that can leak a URL are ones that already
have it via `self._webhook_url`, so exact-substring match catches them. Tests
prove this end-to-end with the sentinel URL.

**Open question for user**: confirm exact-substring redaction is sufficient,
or want a regex matching `services/T.../B.../...`-style Slack webhook paths.

---

## 12. Out of Scope (for this step)

- Microsoft Teams delivery (§10).
- Fingerprint cooldown / dedup (Step 8 owns this).
- Threading / replies to a parent message.
- Slack Web API (`chat.postMessage`) with bot tokens — we only support
  incoming webhooks in v0.
- Honoring Slack `Retry-After` header (§11.2).
- Persisting `DeliveryResult` to disk (Step 8 may add this).
- Multi-channel routing (severity → channel map) — Step 8 candidate.

---

## 13. Complexity Estimate

- New file `slack_alerter.py`: ~220 lines.
- `config.py` delta: ~30 lines (new fields + `_parse_int`).
- New tests in `tests/test_slack_alerter.py`: ~260 lines (~12 tests).
- New tests in `tests/test_config.py`: ~30 lines (~3 tests).
- Total estimated implementation time after plan approval: **3–4 hours**.
- Complexity: **MEDIUM** — formatting is straightforward, but retry +
  redaction + dry-run interplay needs careful test scaffolding.

---

## 14. Ready-for-TDD Checklist

- [x] Acceptance criteria restated and decomposed into testable subgoals (§1).
- [x] New file and modified files enumerated with reasons (§2).
- [x] Public API decision made and justified (§2.5).
- [x] Every new env var listed with default and validation rule (§4.2).
- [x] Block Kit payload shape specified with per-severity examples (§5).
- [x] Retry policy table with backoff schedule and total cap (§6).
- [x] Secret-handling rules enumerated (where, redaction, what's logged) (§7).
- [x] ≥8 tests planned with explicit assertions (§8.1 has 12).
- [x] Coverage target stated (§8.3).
- [x] Phased implementation order mapped to project workflow (§9).
- [x] Risks and open questions surfaced for user confirmation (§11).
- [ ] **User confirmation** to proceed to TESTS phase.

**WAITING FOR CONFIRMATION**: Proceed to Step 2 (TESTS) with this plan?
(yes / no / modify)
