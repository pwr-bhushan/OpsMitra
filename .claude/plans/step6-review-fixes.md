# Step 6: Fix Plan — Address Step 6 Review Findings

**Step 6 Plan reference**: [`.claude/plans/step6-slack-delivery.md`](./step6-slack-delivery.md)
**Review verdict from Step 6**: **APPROVE WITH MINORS**
**Branch**: `dev` (mandatory per `.claude/tasks/lessons.md`)
**Workflow phase**: FIX PLAN (no code in this phase)

---

## 1. Scope of This Fix Pass

The Step 6 `python-review` returned **APPROVE WITH MINORS**. Of the findings
raised, the main session has selected **nine** for this fix pass and
**explicitly deferred** the rest. This plan covers only the selected nine:
defense-in-depth redaction of `urllib` exception messages (H1), strict bounds
checking on the new Slack numeric config (H2), clarification of the
`_post_with_retry` loop fallthrough (H3), surfacing the redacted exception
representation in the final failure log (M3), per-sub-case log isolation in the
URL-leak test (M5), three targeted unit tests for `_backoff_seconds`,
`_should_retry`, and `_redact` (L3), removal of the dead `hasattr` fallback in
`format_alert` (L4), removal of an unused `load_config` import in
`tests/test_slack_alerter.py` (N2), and extraction of the Slack hooks URL
prefix as a module-level constant in `config.py` (N3).

### 1.1 Findings addressed in this fix pass

| #  | Severity | File:Line                                      | One-line description                                                                                  |
|----|----------|------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| H1 | HIGH     | `src/opsmitra/slack_alerter.py:342–348`        | Build `exc_repr` via `_redact(str(exc))` so re-ordering `except` clauses cannot leak `HTTPError.url`.   |
| H2 | HIGH     | `src/opsmitra/config.py:_load_alert_config`     | Raise `ValueError` on out-of-range numeric Slack config (timeout, retries, backoff, cap).             |
| H3 | HIGH     | `src/opsmitra/slack_alerter.py:_post_with_retry`| Restructure (or comment) the loop so the trailing `return last_status, max_attempts` is unambiguous.  |
| M3 | MEDIUM   | `src/opsmitra/slack_alerter.py:_post_with_retry`| Return `(status, attempts, last_exc_repr)` so `send` logs `reason=TimeoutError` instead of `reason=None`. |
| M5 | MEDIUM   | `tests/test_slack_alerter.py`                   | `caplog.clear()` between the 5 sub-cases of `test_webhook_url_never_appears_in_logs_or_exceptions`.    |
| L3 | LOW      | `tests/test_slack_alerter.py` (new tests)       | Add 3 direct unit tests: `_backoff_seconds` cap, `_should_retry` unknown-exception, `_redact` None URL. |
| L4 | LOW      | `src/opsmitra/slack_alerter.py:106–115`         | Drop the `hasattr(window_start, "isoformat")` defensive fallback — `Anomaly.window_start: datetime`.  |
| N2 | NIT      | `tests/test_slack_alerter.py:30`                | Remove the unused `load_config` import.                                                                |
| N3 | NIT      | `src/opsmitra/config.py:84–86`                   | Extract `_SLACK_HOOK_PREFIX = "https://hooks.slack.com/"` and use it in the warning check.            |

### 1.2 Findings explicitly DEFERRED (out of scope here)

Per the main session's instruction, do **NOT** touch these in this pass:

- **M1** — (deferred) Catalogued for Step 10 hardening; not load-bearing on
  Step 6 acceptance.
- **M2** — `MappingProxyType` / payload aliasing concerns for the
  `DeliveryResult.payload` dict. Returning a `MappingProxyType` would break
  the existing dict-typed dataclass field contract and the equality assertions
  in `test_format_alert_is_pure_deterministic` / `test_send_dry_run_returns_payload_no_network`.
  Defer to **Step 10 (hardening)** for a coordinated immutability sweep.
  **Flag for review revisit in Step 10** per main-session instruction.
- **M4** — (deferred) Cosmetic or out-of-scope per main session.
- **L1**, **L2** — (deferred) Low-impact cleanups not blocking acceptance.
- **N1**, **N4** — (deferred) Nit-level cosmetic items.

Rationale (one line each):
- M1: cataloged; not blocking acceptance.
- M2: would require a typing/contract change across the dataclass; safer in a
  dedicated hardening pass.
- M4: cosmetic; defer.
- L1: low impact; defer.
- L2: low impact; defer.
- N1: nit; defer.
- N4: nit; defer.

---

## 2. Finding H1 (HIGH) — Defense-in-depth `exc_repr` redaction in `_post_with_retry`

### 2.1 File & current code

**File**: `/Users/bhushan/Coding/Projects/OpsMitra/src/opsmitra/slack_alerter.py`
**Lines**: 342–353 (the three `except` arms inside `_post_with_retry`)

Current implementation (relevant fragment):

```python
except HTTPError as exc:
    status = exc.code
    last_status = status
    if not self._should_retry(exc):
        return status, attempt
    exc_repr = f"{type(exc).__name__}({status})"

except (URLError, TimeoutError, OSError) as exc:
    last_status = None
    if not self._should_retry(exc):
        return None, attempt
    exc_repr = type(exc).__name__
```

### 2.2 Problem

Today, the `HTTPError` arm constructs `exc_repr` from only `type(exc).__name__`
and `exc.code`, so the URL stored on `HTTPError.url` / `HTTPError.msg` can never
reach the log line. But this safety is purely accidental: a maintainer who
re-orders the `except` arms (e.g. collapsing `HTTPError` into the broader
arm, or adding a new arm above it) could trivially produce `exc_repr = str(exc)`
which **does** contain the URL — `HTTPError.__str__` includes `self.url`.

This is the same class of latent leak the redaction policy in §7 of the Step 6
plan was designed to prevent. The fix is defense-in-depth: always pass the
exception's string form through `self._redact(...)` before storing it in
`exc_repr`, and pin the ordering of `except` arms with a one-line comment.

### 2.3 Exact change

Replace lines 342–353 with:

```python
# Order matters: HTTPError must precede URLError (HTTPError is a subclass of
# URLError). Re-ordering risks routing 4xx/5xx into the broad arm and losing
# the status-code branch. All exception text is redacted via _redact() as
# defense-in-depth so future re-orderings cannot leak the webhook URL via
# HTTPError.url / HTTPError.msg.
except HTTPError as exc:
    status = exc.code
    last_status = status
    if not self._should_retry(exc):
        return status, attempt
    exc_repr = self._redact(f"{type(exc).__name__}({status}): {exc}")

except (URLError, TimeoutError, OSError) as exc:
    last_status = None
    if not self._should_retry(exc):
        return None, attempt
    exc_repr = self._redact(f"{type(exc).__name__}: {exc}")
```

**Rationale**:

- `self._redact(...)` is idempotent on strings that do not contain the
  webhook URL (it's a `str.replace(url, _REDACTED)` no-op when the URL is
  absent), so this change is zero-cost on the happy path.
- Including `str(exc)` in `exc_repr` actually *improves* the observability of
  the WARNING/ERROR log line without changing its security posture, because
  everything routes through `_redact` first.
- The one-line comment documents the load-bearing ordering invariant so a
  future contributor cannot silently break it.

### 2.4 Regression test that proves no behaviour change

The existing test `test_webhook_url_never_appears_in_logs_or_exceptions`
(`tests/test_slack_alerter.py`) covers the URL-leak invariant across
HTTPError 500, URLError, TimeoutError, missing-URL, and 4xx paths. After the
M5 fix below (per-sub-case `caplog.clear()`), this test is the canonical
regression check for H1.

A new helper case is NOT required: the sentinel URL is configured globally
for the test, and the `str(exc)` payload of an `HTTPError` constructed with
`url=sentinel_url` would now flow through `_redact` before reaching `caplog`.

### 2.5 Risk

- **Low**. `_redact` is pure and synchronous; it cannot raise on str input.
  The change adds at most one extra `str.replace(...)` per failed attempt.

---

## 3. Finding H2 (HIGH) — Bounds checking on numeric Slack config in `_load_alert_config`

### 3.1 File & current code

**File**: `/Users/bhushan/Coding/Projects/OpsMitra/src/opsmitra/config.py`
**Function**: `_load_alert_config` (lines 82–107)

Current implementation parses each numeric env var via `_parse_float` /
`_parse_int` (which raise on garbage) but performs **no range validation**.
A user could set `OPSMITRA_SLACK_TIMEOUT_SECONDS=-1` or
`OPSMITRA_SLACK_BACKOFF_MAX_SECONDS=0.1` (less than the base) and the
`SlackAlerter` would silently do the wrong thing at runtime.

### 3.2 Problem

Per `.claude/tasks/lessons.md`: *"Malformed config inputs should raise
`ValueError` to surface misconfiguration loudly."* Out-of-range numeric
values are a class of malformed input that today goes undetected. The fix
must:

1. Raise `ValueError` (not a custom exception class) so it matches the
   existing `test_config_rejects_malformed_model_timeout` precedent.
2. Name the offending field in the error message.
3. Never include the webhook URL or any secret content in the message.
4. Run **after** parsing (so type errors still surface as
   "Invalid float/int value" from `_parse_float` / `_parse_int`).

### 3.3 Exact bounds

| Field                          | Valid range                        | ValueError message                                                                  |
|--------------------------------|------------------------------------|-------------------------------------------------------------------------------------|
| `slack_timeout_seconds`        | `> 0`                              | `"slack_timeout_seconds must be > 0"`                                              |
| `slack_max_retries`            | `>= 0`                             | `"slack_max_retries must be >= 0"`                                                 |
| `slack_backoff_base_seconds`   | `>= 0`                             | `"slack_backoff_base_seconds must be >= 0"`                                        |
| `slack_backoff_max_seconds`    | `>= slack_backoff_base_seconds`    | `"slack_backoff_max_seconds must be >= slack_backoff_base_seconds"`                 |
| `slack_total_wait_cap_seconds` | `>= 0`                             | `"slack_total_wait_cap_seconds must be >= 0"`                                      |

Note: the messages reference field names, never env var names containing
`URL` or `WEBHOOK`, and never include the parsed numeric value (which would
be a minor leak vector if the user set something exotic).

### 3.4 Exact change

Replace the body of `_load_alert_config` (lines 82–107) with:

```python
def _load_alert_config(values: Mapping[str, str]) -> AlertConfig:
    """Build AlertConfig from environment values.

    Warns on unexpected webhook URL prefix and raises ValueError when any
    numeric Slack setting is out of range. Range messages name only the
    offending field — never the URL or its env-var name.
    """
    webhook_url = _optional(values, "OPSMITRA_SLACK_WEBHOOK_URL")
    if webhook_url is not None and not webhook_url.startswith(_SLACK_HOOK_PREFIX):
        logger.warning("OPSMITRA_SLACK_WEBHOOK_URL has unexpected prefix")

    timeout_seconds = _parse_float(
        _get(values, "OPSMITRA_SLACK_TIMEOUT_SECONDS", "5.0")
    )
    max_retries = _parse_int(_get(values, "OPSMITRA_SLACK_MAX_RETRIES", "3"))
    backoff_base = _parse_float(
        _get(values, "OPSMITRA_SLACK_BACKOFF_BASE_SECONDS", "0.5")
    )
    backoff_max = _parse_float(
        _get(values, "OPSMITRA_SLACK_BACKOFF_MAX_SECONDS", "8.0")
    )
    total_wait_cap = _parse_float(
        _get(values, "OPSMITRA_SLACK_TOTAL_WAIT_CAP_SECONDS", "15.0")
    )

    if timeout_seconds <= 0:
        raise ValueError("slack_timeout_seconds must be > 0")
    if max_retries < 0:
        raise ValueError("slack_max_retries must be >= 0")
    if backoff_base < 0:
        raise ValueError("slack_backoff_base_seconds must be >= 0")
    if backoff_max < backoff_base:
        raise ValueError(
            "slack_backoff_max_seconds must be >= slack_backoff_base_seconds"
        )
    if total_wait_cap < 0:
        raise ValueError("slack_total_wait_cap_seconds must be >= 0")

    return AlertConfig(
        slack_webhook_url=webhook_url,
        dry_run=_parse_bool(_get(values, "OPSMITRA_DRY_RUN", "true"), default=True),
        slack_timeout_seconds=timeout_seconds,
        slack_max_retries=max_retries,
        slack_backoff_base_seconds=backoff_base,
        slack_backoff_max_seconds=backoff_max,
        slack_total_wait_cap_seconds=total_wait_cap,
        slack_channel_override=_optional(values, "OPSMITRA_SLACK_CHANNEL_OVERRIDE"),
    )
```

The `_SLACK_HOOK_PREFIX` constant referenced above is introduced by **N3** in §10.

### 3.5 Rationale

- All five bounds checks run **after** parsing, so a malformed string still
  fails with `ValueError: Invalid float value: ...` (existing contract,
  asserted by `test_alert_config_max_retries_malformed_raises_value_error`).
- Each `ValueError` names exactly one field. No URL substring, no env-var
  string, no parsed value — minimum leak surface.
- The chained `if`/`raise` ladder is intentionally not collapsed into a
  single `match` / dict-driven validator: explicit conditions are trivially
  reviewable in the security context.
- `ValueError` (not a custom class) matches the lessons.md guidance and the
  existing model-timeout test precedent.

### 3.6 Regression / negative tests

Existing tests in `tests/test_config.py` that must still pass unchanged:

| Test                                                         | Outcome under new code                                                                  |
|--------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| `test_alert_config_defaults`                                 | All defaults are in-range → no `ValueError`; dataclass equality holds.                  |
| `test_alert_config_env_overrides`                            | All override values are in-range → no `ValueError`.                                     |
| `test_alert_config_max_retries_malformed_raises_value_error` | `"abc"` → still raises `ValueError` from `_parse_int` *before* bounds checks run.        |

**New tests** (added in `tests/test_config.py`, see §6.1):

| Test                                                          | Asserts                                                                                            |
|---------------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| `test_alert_config_timeout_zero_raises_value_error`           | `OPSMITRA_SLACK_TIMEOUT_SECONDS="0"` → `ValueError` mentioning `slack_timeout_seconds`.            |
| `test_alert_config_timeout_negative_raises_value_error`       | `OPSMITRA_SLACK_TIMEOUT_SECONDS="-1.5"` → `ValueError` mentioning `slack_timeout_seconds`.        |
| `test_alert_config_max_retries_negative_raises_value_error`   | `OPSMITRA_SLACK_MAX_RETRIES="-1"` → `ValueError` mentioning `slack_max_retries`.                  |
| `test_alert_config_backoff_base_negative_raises_value_error`  | `OPSMITRA_SLACK_BACKOFF_BASE_SECONDS="-0.1"` → `ValueError` mentioning `slack_backoff_base_seconds`. |
| `test_alert_config_backoff_max_below_base_raises_value_error` | `BASE=2.0`, `MAX=1.0` → `ValueError` mentioning both fields.                                       |
| `test_alert_config_total_wait_cap_negative_raises_value_error` | `OPSMITRA_SLACK_TOTAL_WAIT_CAP_SECONDS="-5"` → `ValueError` mentioning `slack_total_wait_cap_seconds`. |
| `test_alert_config_error_messages_never_contain_url`          | All six error messages above contain neither `"hooks.slack.com"` nor the configured sentinel URL.   |

The last test directly enforces the secret-handling invariant for bounds
errors.

### 3.7 Risk

- **Low**. The bounds are conservative and match `slack_alerter.py`'s implicit
  assumptions (e.g. `_backoff_seconds` would divide nonsensically with a
  negative `base`; `_post_with_retry`'s `attempt < max_attempts` loop would
  be no-op with `max_retries=-1`).
- **No** existing test sets out-of-range values for these fields (verified by
  reading `tests/test_config.py` Step 6 additions), so no in-place test edits
  are required.

---

## 4. Finding H3 (HIGH) — Clarify `_post_with_retry` loop fallthrough

### 4.1 File & current code

**File**: `/Users/bhushan/Coding/Projects/OpsMitra/src/opsmitra/slack_alerter.py`
**Function**: `_post_with_retry` (lines 303–375)

The loop currently does:

1. For each `attempt in range(1, max_attempts + 1)`:
   - Try → return on success.
   - Catch retryable → set `exc_repr`, set `last_status`.
   - `if attempt < max_attempts:` sleep & continue.
2. After the loop: `return last_status, max_attempts`.

The trailing `return last_status, max_attempts` is correct (it's hit when
the last attempt failed and the loop exited naturally), but it's
**implicit**: the reader has to mentally verify that the loop's range upper
bound equals `max_attempts` and that the `if attempt < max_attempts:` guard
ensures no further sleep after the final attempt. A reviewer rightly flagged
this as a maintainability hazard.

### 4.2 Problem

Two viable fixes:
- **(A) Restructure**: detect `attempt == max_attempts` inside the loop body
  and `return` there, eliminating the trailing statement.
- **(B) Comment**: leave the structure intact and add a comment explaining
  the invariant.

Restructuring risks subtle behavioural drift (e.g. accidentally returning
before `last_status` has been updated for the final attempt's exception).
The conservative choice — given that the loop is correct today and is
covered by `test_send_retries_on_timeout_then_gives_up` — is **(B) Comment**
with a small additional restructure: move the trailing `return` literally
adjacent to the loop's last meaningful statement so the control flow is
obvious. We will **not** delete the trailing return.

### 4.3 Exact change

Replace lines 355–375 (the trailing fragment of `_post_with_retry`) with:

```python
            # Decide whether to sleep before the next attempt. On the final
            # iteration (attempt == max_attempts) we intentionally skip the
            # sleep so the loop exits naturally and falls through to the
            # trailing return below — this is the "exhausted retries" path.
            if attempt < max_attempts:
                backoff = self._backoff_seconds(attempt)
                if elapsed_wait + backoff > self._config.slack_total_wait_cap_seconds:
                    logger.warning(
                        "Slack send total wait cap reached; giving up after %d attempt(s)",
                        attempt,
                    )
                    return last_status, attempt
                logger.warning(
                    "Slack send transient failure (attempt=%d/%d, reason=%s); "
                    "backing off %.2fs",
                    attempt,
                    max_attempts,
                    exc_repr,
                    backoff,
                )
                self._sleep(backoff)
                elapsed_wait += backoff

        # Loop exhausted: every attempt failed with a retryable error. Return
        # the last observed status (None for network-level failures) and the
        # full attempt count.
        return last_status, max_attempts
```

**Behaviour preserved**:

- The number of attempts, the sleep schedule, the early-cap return, and the
  trailing exhausted-retries return are all unchanged.
- The two comments make the load-bearing invariants explicit:
  - skipping the sleep on the final iteration is intentional;
  - the trailing return is the "all attempts failed" path, not dead code.

### 4.4 Why not restructure to return-inside-loop

A naive restructure would be:

```python
            if attempt == max_attempts:
                return last_status, attempt
            # ... sleep and continue
```

This is **functionally equivalent today** but is more fragile: any future
change that adds work after the `if attempt < max_attempts:` block (e.g. a
metrics emit) would need to be duplicated above the early return, or risk
regression. The comment-only fix preserves the single sequential exit point
of the loop body.

### 4.5 Regression test

No new test required. `test_send_retries_on_timeout_then_gives_up` exercises
exactly this fallthrough path; its assertion `attempts == max_retries + 1`
ensures the trailing return is still hit. After this edit, the test must
pass with no changes to its body.

---

## 5. Finding M3 (MEDIUM) — Surface `exc_repr` through `_post_with_retry` return tuple

### 5.1 File & current code

**File**: `/Users/bhushan/Coding/Projects/OpsMitra/src/opsmitra/slack_alerter.py`
**Functions**: `_post_with_retry` (returns `tuple[int | None, int]`) and
`send` (calls it).

Current final-failure log line (line 286–290):

```python
logger.error(
    "Slack send failed after %d attempts (reason=%s)",
    attempts,
    self._redact(str(status_code)),
)
```

For a network-level failure (URLError, TimeoutError), `status_code is None`,
so the log reads `reason=None`, which is operationally useless: the on-call
cannot distinguish "DNS failure" from "TCP timeout" from "TLS handshake
failure" from the alerter's perspective.

### 5.2 Problem

The H1 fix already constructs a redacted `exc_repr` per attempt inside
`_post_with_retry`. The cheapest, safest way to surface it is to widen the
return tuple from `(status, attempts)` to `(status, attempts, last_exc_repr)`
and plumb `last_exc_repr` into the final ERROR log line.

### 5.3 Exact change — `_post_with_retry`

1. Update the return type:
   ```python
   def _post_with_retry(
       self, payload: dict[str, Any]
   ) -> tuple[int | None, int, str | None]:
   ```
2. Track `last_exc_repr: str | None = None` next to `last_status`:
   ```python
   max_attempts = self._config.slack_max_retries + 1
   elapsed_wait = 0.0
   last_status: int | None = None
   last_exc_repr: str | None = None
   ```
3. In each `except` arm, after computing `exc_repr` (post H1 redaction),
   assign `last_exc_repr = exc_repr`.
4. In the non-2xx, non-retryable HTTP branch (line 337–338) the current code
   `return status, attempt` — leave that unchanged; the `send` ERROR log for
   a 4xx already includes the status code, so `exc_repr` for that path is
   not needed. Return `(status, attempt, None)`.
5. Update all `return` sites to include the third element:
   - Success: `return status, attempt, None`
   - Non-retryable 4xx (status branch): `return status, attempt, None`
   - Non-retryable 4xx (HTTPError branch): `return status, attempt, None`
   - Non-retryable network (URLError etc.): `return None, attempt, None`
   - Early total-wait-cap: `return last_status, attempt, last_exc_repr`
   - Trailing exhausted-retries: `return last_status, max_attempts, last_exc_repr`

### 5.4 Exact change — `send`

Replace the network-send block (lines 277–297) with:

```python
status_code, attempts, last_exc_repr = self._post_with_retry(payload)
delivered = status_code is not None and 200 <= status_code < 300
if delivered:
    logger.info(
        "Slack alert delivered (severity=%s, attempts=%d)",
        summary.severity,
        attempts,
    )
else:
    # last_exc_repr is already redacted (see _post_with_retry); if None
    # (HTTP-status failure with no exception captured), fall back to the
    # redacted status code so the log line is never just "reason=None".
    reason = (
        last_exc_repr
        if last_exc_repr is not None
        else self._redact(str(status_code))
    )
    logger.error(
        "Slack send failed after %d attempts (reason=%s)",
        attempts,
        reason,
    )
return DeliveryResult(
    delivered=delivered,
    dry_run=False,
    attempts=attempts,
    status_code=status_code,
    payload=payload,
)
```

### 5.5 Regression / negative tests

- `test_send_retries_on_timeout_then_gives_up` — must still pass. Its
  assertions are on `attempts`, `status_code`, and `delivered`. The change
  only enriches the log line (which the test does not assert on).
- `test_webhook_url_never_appears_in_logs_or_exceptions` — must still pass.
  Because `last_exc_repr` is already redacted at the source (H1), the new
  log path is sentinel-free by construction.
- **Optional add** (not required): a `caplog.text` assertion in the timeout
  test that the final ERROR line includes `"TimeoutError"` instead of
  `"None"`. **Defer** as cosmetic — the structural test already proves M3.

### 5.6 Risk

- **Low**. The change is a pure-tuple widening with no new exception paths.
- The `DeliveryResult` dataclass is unchanged (no new field).

---

## 6. Finding M5 (MEDIUM) — `caplog.clear()` between sub-cases in URL-leak test

### 6.1 File & current code

**File**: `/Users/bhushan/Coding/Projects/OpsMitra/tests/test_slack_alerter.py`
**Test**: `test_webhook_url_never_appears_in_logs_or_exceptions`

The test exercises five failure paths (configuration error, HTTPError 500,
HTTPError 403, URLError, TimeoutError) against the same `caplog` capture
buffer. If sub-case 1 leaks the sentinel and sub-case 5 passes, the failing
assertion at the end blames *all five* paths — so the reviewer cannot tell
which path leaked.

### 6.2 Exact change

Between each sub-case (i.e. before each new `with ...` / `alerter.send(...)`
invocation block) insert:

```python
caplog.clear()
```

After each sub-case, assert the sentinel is absent in **both** `caplog.text`
**and** in any captured exception's `str(exc_info.value)`. The current
end-of-test assertion remains as a final belt-and-braces check across all
captures.

### 6.3 Rationale

- Per-sub-case isolation: any future regression points to *which* path
  leaked, not a global "something leaked".
- No new log records are added or suppressed; only the boundary between
  sub-cases is sharpened.

### 6.4 Risk

- **Low**. `caplog.clear()` is a stdlib pytest fixture API; no version
  considerations.

---

## 7. Finding L3 (LOW) — Add three direct unit tests for private helpers

### 7.1 Background

Step 6 review noted that `_backoff_seconds`, `_should_retry`, and `_redact`
are currently exercised only indirectly through the retry tests. While line
coverage is satisfied, the branches are not directly asserted. The three
small tests below close that gap.

### 7.2 New tests in `tests/test_slack_alerter.py`

| Test                                                         | Inputs                                                                                                            | Expected                                                                                       |
|--------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| `test_backoff_seconds_caps_at_max`                           | Construct `SlackAlerter` with `slack_backoff_base_seconds=0.5`, `slack_backoff_max_seconds=8.0`. Call `_backoff_seconds(attempt)` for `attempt in [1, 2, 3, 4, 5, 6]`. | Returns `[0.5, 1.0, 2.0, 4.0, 8.0, 8.0]` — last two values equal to the cap.                  |
| `test_should_retry_unknown_exception_returns_false`          | Construct `SlackAlerter` with defaults. Call `_should_retry(ValueError("custom"))`.                              | Returns `False`.                                                                                |
| `test_redact_with_none_webhook_url_returns_text_unchanged`   | Construct `SlackAlerter` with `slack_webhook_url=None`. Call `_redact("Failed to POST https://hooks.slack.com/services/SECRET").` | Returns the input string unchanged (no replacement when URL is None).                          |

### 7.3 Test scaffolding

Each test uses the existing `_make_alert_config(...)` factory pattern (or
equivalent — adopt whatever helper `tests/test_slack_alerter.py` already
defines for building `AlertConfig`). Required factory parameters: at minimum
`slack_webhook_url`, `slack_backoff_base_seconds`, `slack_backoff_max_seconds`.

### 7.4 Rationale

- Direct branch assertion: future regressions on these helpers fail
  immediately in their dedicated test rather than as a confusing failure in
  a retry-orchestration test.
- Each test is ~5–8 lines; total addition ~25 lines.

### 7.5 Risk

- **None**. These are new tests against existing public-via-self methods;
  no production code changes required.

---

## 8. Finding L4 (LOW) — Drop dead `hasattr` fallback in `format_alert`

### 8.1 File & current code

**File**: `/Users/bhushan/Coding/Projects/OpsMitra/src/opsmitra/slack_alerter.py`
**Lines**: 106–115

Current:

```python
window_start_iso = (
    anomaly.window_start.isoformat()
    if hasattr(anomaly.window_start, "isoformat")
    else str(anomaly.window_start)
)
window_end_iso = (
    anomaly.window_end.isoformat()
    if hasattr(anomaly.window_end, "isoformat")
    else str(anomaly.window_end)
)
```

### 8.2 Problem

`Anomaly.window_start` and `Anomaly.window_end` are statically typed
`datetime` per `src/opsmitra/models.py`. `datetime.isoformat()` is a
guaranteed method. The `hasattr` fallback is therefore dead code that:
1. Implies the field can be of some other type (it can't).
2. Masks any future type drift instead of failing loudly.

### 8.3 Exact change

Replace lines 106–115 with:

```python
window_start_iso = anomaly.window_start.isoformat()
window_end_iso = anomaly.window_end.isoformat()
```

### 8.4 Regression test

Existing test `test_format_alert_block_kit_structure` asserts the Window
context block contents. Under the new code, `window_start.isoformat()` is
called directly; output is **identical** because the existing test's
`Anomaly` already passes `datetime` instances.

### 8.5 Risk

- **Very low**. If a caller ever constructs an `Anomaly` bypassing the type
  hint with a non-`datetime` `window_start`, an `AttributeError` will now
  surface at format time. That's the desired behaviour — silent
  `str(...)` coercion was hiding a programming error.

---

## 9. Finding N2 (NIT) — Remove unused `load_config` import

### 9.1 File & current code

**File**: `/Users/bhushan/Coding/Projects/OpsMitra/tests/test_slack_alerter.py`
**Line**: 30

Current:

```python
from opsmitra.config import AlertConfig, load_config
```

A `grep -n "load_config" tests/test_slack_alerter.py` confirms `load_config`
appears only on this import line — the symbol is never referenced.

### 9.2 Exact change

Replace line 30 with:

```python
from opsmitra.config import AlertConfig
```

### 9.3 Risk

- **None**. Removing an unused import cannot change runtime behaviour.
- Verifies `ruff check tests/test_slack_alerter.py` F401 is silent on this
  file.

---

## 10. Finding N3 (NIT) — Extract `_SLACK_HOOK_PREFIX` module constant in `config.py`

### 10.1 File & current code

**File**: `/Users/bhushan/Coding/Projects/OpsMitra/src/opsmitra/config.py`
**Lines**: 84–86 (the prefix check inside `_load_alert_config`)

Current:

```python
if webhook_url is not None and not webhook_url.startswith("https://hooks.slack.com/"):
    logger.warning("OPSMITRA_SLACK_WEBHOOK_URL has unexpected prefix")
```

### 10.2 Problem

The string literal `"https://hooks.slack.com/"` is hardcoded inline. Per
`rules/common/coding-style.md`: *"No hardcoded values (use constants or
config)."* Extracting a module constant also lets tests reference the
canonical prefix without re-typing it.

### 10.3 Exact change

1. At the top of `src/opsmitra/config.py`, after the imports and before the
   first dataclass, add:

   ```python
   # Canonical Slack incoming-webhook URL prefix. Used only for shape
   # validation (warn-only). Slack Enterprise Grid + relay proxies may use
   # alternate domains, so this is informational, not a hard requirement.
   _SLACK_HOOK_PREFIX = "https://hooks.slack.com/"
   ```

2. In `_load_alert_config`, replace the inline string with the constant
   (already shown in §3.4 of this plan):

   ```python
   if webhook_url is not None and not webhook_url.startswith(_SLACK_HOOK_PREFIX):
       logger.warning("OPSMITRA_SLACK_WEBHOOK_URL has unexpected prefix")
   ```

### 10.4 Risk

- **None**. Pure refactor; no behavioural change. Tests that don't reference
  the prefix are unaffected.

---

## 11. New & Updated Tests — Consolidated List

### 11.1 New tests in `tests/test_slack_alerter.py` (3 from L3)

1. `test_backoff_seconds_caps_at_max`
2. `test_should_retry_unknown_exception_returns_false`
3. `test_redact_with_none_webhook_url_returns_text_unchanged`

### 11.2 New tests in `tests/test_config.py` (7 from H2)

1. `test_alert_config_timeout_zero_raises_value_error`
2. `test_alert_config_timeout_negative_raises_value_error`
3. `test_alert_config_max_retries_negative_raises_value_error`
4. `test_alert_config_backoff_base_negative_raises_value_error`
5. `test_alert_config_backoff_max_below_base_raises_value_error`
6. `test_alert_config_total_wait_cap_negative_raises_value_error`
7. `test_alert_config_error_messages_never_contain_url`

### 11.3 Updated test in `tests/test_slack_alerter.py` (1 from M5)

- `test_webhook_url_never_appears_in_logs_or_exceptions` — insert
  `caplog.clear()` between each of the 5 sub-cases, and add per-sub-case
  sentinel-absent assertions.

### 11.4 Test plan

- **No existing test should regress.** All Step 6 tests assert structural
  properties (status_code, attempts, delivered, payload shape) that are
  preserved by every change in this plan.
- **`ValueError` is the correct exception class for the bounds checks** —
  no stronger class is needed:
  - It matches the existing `test_config_rejects_malformed_model_timeout`
    precedent.
  - It matches `lessons.md` guidance.
  - A custom `AlertConfigError` would be over-engineering for a single
    config layer.
- **No test of `_load_alert_config` currently passes out-of-range numerics**
  — verified by grep. Bounds checks therefore do not break any existing
  fixtures; only the 7 new tests in §11.2 exercise them.

---

## 12. Acceptance

After the FIX phase runs and RE-VERIFY completes:

1. **All tests pass**: `pytest -q` shows green. Existing Step 6 tests +
   3 new L3 tests + 7 new H2 tests + the updated M5 test.
2. **Coverage targets sustained**:
   - `src/opsmitra/slack_alerter.py` ≥ **92%** (no regression from the
     Step 6 baseline; M3/H1/H3/L4 only add comments and tuple plumbing
     covered by existing tests + L3's direct tests).
   - `src/opsmitra/config.py` = **100%** (sustained — the H2 branches are
     fully covered by the 6 new bounds tests; N3 is a pure rename).
3. **Original Step 6 acceptance criteria still hold**:
   - Alert formatting is tested without sending real messages. ✓ (L4 only
     simplifies an internal branch; pure-function contract preserved.)
   - Webhook URL is read from environment/config only. ✓ (no API change.)
   - Dry-run mode prints the final alert payload. ✓ (no change to dry-run
     branch.)
   - Retry on transient errors only; no retry on 4xx. ✓ (H3 adds a
     comment; no control-flow change.)
   - Webhook URL never appears in logs/exceptions. ✓ (H1 adds
     defense-in-depth; M3 surfaces redacted reps; M5 sharpens the test.)
4. **`ruff` + `black` clean** on `src/` and `tests/`.

---

## 13. Deferred Items (one-line reasons)

| #  | Severity | One-line reason for deferral                                                                                                                |
|----|----------|---------------------------------------------------------------------------------------------------------------------------------------------|
| M1 | MEDIUM   | Out of scope for the security-and-clarity sweep this fix pass targets; not blocking Step 6 acceptance.                                       |
| M2 | MEDIUM   | `MappingProxyType` / payload-aliasing change would alter the `DeliveryResult.payload: dict` contract and break existing equality assertions. **Flag for Step 10 (hardening) review revisit** per main-session instruction. |
| M4 | MEDIUM   | Cosmetic or non-blocking; defer to a later sweep.                                                                                            |
| L1 | LOW      | Low impact; defer until after Step 8 scheduler work resettles the surface.                                                                   |
| L2 | LOW      | Low impact; defer.                                                                                                                            |
| N1 | NIT      | Cosmetic; defer.                                                                                                                              |
| N4 | NIT      | Cosmetic; defer.                                                                                                                              |

---

## 14. Risks

### 14.1 H1 redaction string drift

If a future change to `_redact` (e.g. switching to regex matching) breaks
idempotency on non-URL strings, the WARNING log lines would corrupt.
**Mitigation**: `_redact` is currently a single `str.replace(self._webhook_url, _REDACTED)`
which is provably idempotent. No regex changes are in scope.

### 14.2 H2 bounds tightening

If a legitimate ops scenario requires `slack_backoff_max_seconds <
slack_backoff_base_seconds` (e.g. base=10, max=5 to disable exponential
growth), the new bounds check blocks it. **Mitigation**: this is a
nonsensical configuration; the user can set `slack_max_retries=0` instead.
Documented in the error message.

### 14.3 H3 comment-only restructure

If a future contributor *does* restructure the loop without reading the
comment, the trailing return could become unreachable. **Mitigation**: the
comment specifically names the "exhausted retries" path, and the
`test_send_retries_on_timeout_then_gives_up` test would fail loudly if the
return were deleted. No additional guard needed.

### 14.4 M3 tuple widening

If any **out-of-tree** caller imports `_post_with_retry` and unpacks the
return tuple, the widening from 2-tuple to 3-tuple breaks them. **Mitigation**:
`_post_with_retry` is private (leading underscore); no out-of-tree callers
exist (verified by grep across `src/` and `tests/`).

### 14.5 L4 typing strictness

If a downstream caller constructs an `Anomaly` with a non-`datetime`
`window_start` (bypassing the type hint), the new code raises
`AttributeError` instead of silently coercing to `str(...)`. **Mitigation**:
this is the *desired* behaviour. Type drift should surface loudly.

### 14.6 N3 constant location

If the constant is placed inside `_load_alert_config` instead of at module
top, the warning check still works but no other function can reference it.
**Mitigation**: the plan explicitly specifies module-top placement (§10.3).

---

## 15. FIX-AGENT INSTRUCTIONS (haiku)

**Read this section verbatim. Do exactly these edits, in this order, then
stop.**

### 15.1 Pre-flight (no edits yet)

1. Confirm you are on branch `dev`:
   ```bash
   cd /Users/bhushan/Coding/Projects/OpsMitra && git rev-parse --abbrev-ref HEAD
   ```
   Must print `dev`. If not, stop and report.

2. Confirm the four target files exist:
   - `/Users/bhushan/Coding/Projects/OpsMitra/src/opsmitra/slack_alerter.py`
   - `/Users/bhushan/Coding/Projects/OpsMitra/src/opsmitra/config.py`
   - `/Users/bhushan/Coding/Projects/OpsMitra/tests/test_slack_alerter.py`
   - `/Users/bhushan/Coding/Projects/OpsMitra/tests/test_config.py`

### 15.2 Edit order

Apply edits in this exact order to keep each step small and verifiable:

1. **N2** (1 line) — remove `load_config` from `tests/test_slack_alerter.py` import.
2. **N3** — add `_SLACK_HOOK_PREFIX` constant to `src/opsmitra/config.py` and use it.
3. **H2** — refactor `_load_alert_config` to add bounds checks (5 new
   `raise ValueError(...)` lines + variable extraction).
4. **L4** — drop the `hasattr` fallback in `format_alert` (2 multi-line
   ternaries → 2 single-line assignments).
5. **H1** — add the ordering comment and route both `except` arms'
   `exc_repr` through `self._redact(...)`.
6. **H3** — add the two comments around the `if attempt < max_attempts:`
   block and the trailing `return last_status, max_attempts`.
7. **M3** — widen `_post_with_retry` return tuple to
   `(status, attempts, last_exc_repr)`; update all 6 return sites; update
   `send` to consume the third element and build the `reason` for the
   final ERROR log.
8. **M5** — add `caplog.clear()` between sub-cases of
   `test_webhook_url_never_appears_in_logs_or_exceptions` and the
   per-sub-case sentinel-absent assertions.
9. **L3** (3 new tests) — append to `tests/test_slack_alerter.py`.
10. **H2 tests** (7 new tests) — append to `tests/test_config.py`.

### 15.3 Post-flight verification (run from repo root)

```bash
cd /Users/bhushan/Coding/Projects/OpsMitra
ruff check src/ tests/
black --check src/ tests/
pytest -q --cov=src --cov-report=term-missing
```

Required outcomes:
- `ruff check`: no errors.
- `black --check`: clean (no reformat needed).
- `pytest`: all tests pass (existing + 10 new).
- Coverage on `src/opsmitra/slack_alerter.py` ≥ **92%** (sustained).
- Coverage on `src/opsmitra/config.py` = **100%** (sustained).

If any check fails:
1. Do **not** "fix it harder" by adding new code paths.
2. Stop and report which check failed, with the full failing output.
3. The main session will decide whether to revise the plan.

### 15.4 Stop conditions

After successful post-flight, report:
- The list of files edited.
- The `pytest` summary line (e.g. `34 passed in 0.6s`).
- The coverage % for `slack_alerter.py` and `config.py`.
- Do NOT commit. Do NOT push. Do NOT advance to the next workflow step.
  The main session will run Step 8 (RE-VERIFY) via the `verify` skill, then
  ask the user before any commit.

---

## 16. Out of Scope for This Fix Pass

- The seven deferred review findings (see §1.2 and §13).
- Any new features, refactors, or doc updates beyond what is enumerated above.
- Coverage uplift beyond maintaining ≥92% on `slack_alerter.py` and 100% on
  `config.py`.
- Commit/push/PR — those happen only after user confirmation post-verify.

---

## 17. Ready-for-FIX Checklist

- [x] All nine selected findings have a precise file:line target.
- [x] Each finding has an exact change (old → new) with reasoning.
- [x] Each finding names existing tests that prove no regression.
- [x] New tests enumerated (3 from L3 + 7 from H2 + 1 updated from M5).
- [x] Test plan confirms `ValueError` is the correct exception class.
- [x] Deferred items listed with one-line reasons; M2 flagged for Step 10.
- [x] Risks enumerated (redaction drift, bounds tightening, comment-only
      restructure, tuple widening, typing strictness, constant placement).
- [x] FIX-agent instructions are sequenced and unambiguous.
- [x] Post-flight verification command set is explicit.
- [ ] **User confirmation** to proceed to Step 7 (FIX, haiku agent).

**WAITING FOR CONFIRMATION**: Proceed to Step 7 (FIX) with this plan?
(yes / no / modify)
