"""Slack incoming-webhook alert delivery for OpsMitra anomaly alerts.

Converts an AlertSummary + Anomaly pair into a Block Kit message and POSTs it
to the configured Slack incoming-webhook URL with bounded exponential backoff.

Design decisions:
- stdlib only (urllib): consistent with summarizer.py, zero new deps.
- format_alert is a pure function: testable without any I/O.
- SlackAlerter accepts injectable sleep/opener: retry tests run without waits.
- _redact scrubs the webhook URL from every log line and re-raised exception.
- dry_run (default True) prevents CI from ever sending real Slack messages.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from opsmitra.config import AlertConfig
from opsmitra.models import Anomaly
from opsmitra.summarizer import AlertSummary

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_REDACTED = "[REDACTED_WEBHOOK_URL]"

_SEVERITY_EMOJI: dict[str, str] = {
    "low": ":information_source:",
    "medium": ":warning:",
    "high": ":rotating_light:",
    "critical": ":fire:",
}

_SEVERITY_COLOR: dict[str, str] = {
    "low": "#36a64f",
    "medium": "#daa038",
    "high": "#d93f0b",
    "critical": "#b30000",
}

_MAX_EVIDENCE_BULLETS = 5
_DEFAULT_USER_AGENT = "opsmitra-slack-alerter/1.0"


# ---------------------------------------------------------------------------
# Result and error types (immutable per coding-style rules)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeliveryResult:
    """Immutable result of a Slack alert send attempt."""

    delivered: bool
    dry_run: bool
    attempts: int
    status_code: int | None
    payload: dict[str, Any]


class SlackConfigurationError(ValueError):
    """Raised when AlertConfig is invalid for sending (e.g. missing webhook URL
    outside dry-run mode)."""


# ---------------------------------------------------------------------------
# Pure formatting helper
# ---------------------------------------------------------------------------


def format_alert(
    summary: AlertSummary,
    anomaly: Anomaly,
    *,
    channel: str | None = None,
) -> dict[str, Any]:
    """Return the Block Kit JSON payload for the given alert.

    Pure function: no I/O, no clock, no RNG. Two calls with the same inputs
    always produce equal dicts.

    Args:
        summary: The AlertSummary produced by the Summarizer.
        anomaly: The originating Anomaly record.
        channel: Optional Slack channel override (injected by SlackAlerter.send
                 from AlertConfig.slack_channel_override). When None the
                 ``channel`` key is omitted from the payload.

    Returns:
        Block Kit payload dict ready for json.dumps.
    """
    emoji = _SEVERITY_EMOJI.get(summary.severity, ":bell:")
    color = _SEVERITY_COLOR.get(summary.severity, "#888888")
    tenant_id = anomaly.tenant_id if anomaly.tenant_id is not None else "unknown"

    # Format window timestamps as ISO-8601 strings
    window_start_iso = anomaly.window_start.isoformat()
    window_end_iso = anomaly.window_end.isoformat()

    bullets = _evidence_bullets(anomaly)

    header_block = {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{emoji} {summary.title}",
            "emoji": True,
        },
    }

    context_block = {
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"*Tenant:* `{tenant_id}`"},
            {"type": "mrkdwn", "text": f"*Type:* `{anomaly.type}`"},
            {"type": "mrkdwn", "text": f"*Confidence:* `{summary.confidence}`"},
            {
                "type": "mrkdwn",
                "text": f"*Window:* `{window_start_iso} → {window_end_iso}`",
            },
        ],
    }

    evidence_text = (
        "*Summary*\n"
        + summary.summary
        + "\n\n*Evidence*\n"
        + "\n".join(f"• {b}" for b in bullets)
    )
    evidence_block = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": evidence_text},
    }

    action_block = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*Suggested action:* {summary.recommended_action}\n"
                f"_Likely cause:_ {summary.likely_cause}"
            ),
        },
    }

    payload: dict[str, Any] = {
        "blocks": [header_block, context_block, evidence_block, action_block],
        "attachments": [
            {
                "color": color,
                "fallback": summary.title,
            }
        ],
        "text": summary.title,
    }

    if channel is not None:
        payload["channel"] = channel

    return payload


def _evidence_bullets(anomaly: Anomaly) -> list[str]:
    """Build evidence bullet strings from an Anomaly, capped at _MAX_EVIDENCE_BULLETS.

    Never includes raw Event objects — only str()-coerced scalar fields.
    """
    bullets: list[str] = []

    notes = anomaly.evidence.get("notes") or []
    for note in notes:
        bullets.append(str(note))
        if len(bullets) >= _MAX_EVIDENCE_BULLETS:
            return bullets

    if anomaly.observed and len(bullets) < _MAX_EVIDENCE_BULLETS:
        bullets.append(f"observed: {anomaly.observed}")

    if anomaly.baseline and len(bullets) < _MAX_EVIDENCE_BULLETS:
        bullets.append(f"baseline: {anomaly.baseline}")

    if anomaly.ratio is not None and len(bullets) < _MAX_EVIDENCE_BULLETS:
        bullets.append(f"ratio: {anomaly.ratio:.2f}x")

    return bullets[:_MAX_EVIDENCE_BULLETS]


# ---------------------------------------------------------------------------
# SlackAlerter class
# ---------------------------------------------------------------------------


class SlackAlerter:
    """Deliver Slack alerts via an incoming webhook with retry and redaction."""

    def __init__(
        self,
        config: AlertConfig,
        *,
        sleep: Callable[[float], None] = time.sleep,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        """Initialise SlackAlerter.

        Args:
            config: AlertConfig containing the webhook URL and retry settings.
            sleep: Injectable sleep callable for testing (default: time.sleep).
            opener: Injectable urllib opener for testing (default: urlopen).
        """
        self._config = config
        self._sleep = sleep
        # Store None to trigger lazy module-level urlopen lookup at send time,
        # so patch("opsmitra.slack_alerter.urlopen", ...) works even when the
        # alerter is constructed before the patch context is entered.
        self._opener: Callable[..., Any] | None = opener
        self._webhook_url = config.slack_webhook_url

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, summary: AlertSummary, anomaly: Anomaly) -> DeliveryResult:
        """Build the payload and deliver it to Slack.

        Args:
            summary: The AlertSummary produced by the Summarizer.
            anomaly: The originating Anomaly record.

        Returns:
            DeliveryResult describing the outcome.

        Raises:
            SlackConfigurationError: When webhook URL is absent in non-dry-run mode.
        """
        payload = format_alert(
            summary, anomaly, channel=self._config.slack_channel_override
        )

        if self._config.dry_run:
            logger.info(
                "dry-run: would send Slack alert (severity=%s, type=%s) %s",
                summary.severity,
                anomaly.type,
                json.dumps(payload),
            )
            return DeliveryResult(
                delivered=True,
                dry_run=True,
                attempts=0,
                status_code=None,
                payload=payload,
            )

        if self._webhook_url is None:
            logger.error("Slack send aborted: webhook URL not configured")
            raise SlackConfigurationError(
                "Slack send aborted: webhook URL not configured"
            )

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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _post_with_retry(
        self, payload: dict[str, Any]
    ) -> tuple[int | None, int, str | None]:
        """POST payload to the webhook with bounded exponential backoff.

        Args:
            payload: Block Kit dict to serialize and send.

        Returns:
            Tuple of (status_code_or_None, attempts, last_exc_repr_or_None).
            last_exc_repr is already redacted via _redact(); None on success or
            when only an HTTP status (no exception) drove the failure.
        """
        max_attempts = self._config.slack_max_retries + 1
        elapsed_wait = 0.0
        last_status: int | None = None
        last_exc_repr: str | None = None

        req = Request(
            url=self._webhook_url,  # type: ignore[arg-type]
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": _DEFAULT_USER_AGENT,
            },
            method="POST",
        )

        # Resolve opener lazily so patch("opsmitra.slack_alerter.urlopen", ...)
        # is honoured even when the alerter was constructed before the patch.
        _open = self._opener if self._opener is not None else urlopen

        for attempt in range(1, max_attempts + 1):
            try:
                with _open(req, timeout=self._config.slack_timeout_seconds) as response:
                    status = response.getcode()
                if 200 <= status < 300:
                    return status, attempt, None
                # Non-2xx, non-retryable (4xx, 3xx, 429)
                if not self._should_retry(status):
                    return status, attempt, None
                last_status = status
                exc_repr = f"HTTP {status}"

            # Order matters: HTTPError must precede URLError (HTTPError is a subclass of
            # URLError). Re-ordering risks routing 4xx/5xx into the broad arm and losing
            # the status-code branch. All exception text is redacted via _redact() as
            # defense-in-depth so future re-orderings cannot leak the webhook URL via
            # HTTPError.url / HTTPError.msg.
            except HTTPError as exc:
                status = exc.code
                last_status = status
                if not self._should_retry(exc):
                    return status, attempt, None
                exc_repr = self._redact(f"{type(exc).__name__}({status}): {exc}")
                last_exc_repr = exc_repr

            except (URLError, TimeoutError, OSError) as exc:
                last_status = None
                if not self._should_retry(exc):
                    return None, attempt, None
                exc_repr = self._redact(f"{type(exc).__name__}: {exc}")
                last_exc_repr = exc_repr

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
                    return last_status, attempt, last_exc_repr
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
        return last_status, max_attempts, last_exc_repr

    def _should_retry(self, exc_or_status: int | Exception) -> bool:
        """Return True if the error is transient and worth retrying.

        Retryable:  5xx HTTP status, URLError, TimeoutError, OSError.
        Not retryable: 4xx (including 429), 3xx, 2xx, unknown exceptions.
        """
        if isinstance(exc_or_status, int):
            return 500 <= exc_or_status < 600
        if isinstance(exc_or_status, HTTPError):
            return 500 <= exc_or_status.code < 600
        if isinstance(exc_or_status, (URLError, TimeoutError, OSError)):
            return True
        return False

    def _backoff_seconds(self, attempt: int) -> float:
        """Compute exponential backoff for the given attempt number.

        Args:
            attempt: 1-based attempt index (backoff before attempt+1).

        Returns:
            Seconds to wait, capped at slack_backoff_max_seconds.
        """
        base = self._config.slack_backoff_base_seconds
        cap = self._config.slack_backoff_max_seconds
        return min(base * (2 ** (attempt - 1)), cap)

    def _redact(self, text: str) -> str:
        """Replace the configured webhook URL with _REDACTED in text.

        Safe when self._webhook_url is None (returns text unchanged).
        """
        if self._webhook_url is None:
            return text
        return text.replace(self._webhook_url, _REDACTED)
