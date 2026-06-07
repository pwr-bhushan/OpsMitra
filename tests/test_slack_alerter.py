"""RED-phase tests for opsmitra.slack_alerter (module does not exist yet).

All tests in this file are expected to FAIL at import time until
src/opsmitra/slack_alerter.py is implemented (Step 6, Phase B–E).

Mocking idioms mirror tests/test_summarizer.py exactly:
- MagicMock for the urllib response context manager
- patch("opsmitra.slack_alerter.urlopen", ...)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError, URLError

import pytest

from opsmitra.models import Anomaly
from opsmitra.summarizer import AlertSummary

# These imports are expected to fail (ImportError) until the module exists.
from opsmitra.slack_alerter import (  # noqa: E402
    DeliveryResult,
    SlackAlerter,
    SlackConfigurationError,
    format_alert,
)
from opsmitra.config import AlertConfig


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _anomaly(
    *,
    severity: str = "high",
    tenant_id: str | None = "tenant_acme",
    notes: list[str] | None = None,
) -> Anomaly:
    """Build a stable Anomaly for tests."""
    if notes is None:
        notes = ["single API key generated unusual SMS volume"]
    return Anomaly(
        id="anom_test_1",
        type="sms_abuse_spike",
        severity=severity,
        window_start=datetime(2026, 5, 28, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 28, 11, tzinfo=timezone.utc),
        tenant_id=tenant_id,
        subject={"api_key_id": "key_abused", "endpoint": "/sms/send"},
        observed={"count": 600, "cost_units": 600.0},
        baseline={"hourly_count": 40},
        ratio=15.0,
        evidence={"sample_request_ids": ["req_1"], "notes": notes},
        recommended_actions=["Temporarily throttle the API key"],
    )


def _summary(*, severity: str = "high") -> AlertSummary:
    """Build a stable AlertSummary for tests."""
    return AlertSummary(
        title="SMS spike detected",
        severity=severity,
        summary="Tenant tenant_acme sent unusual SMS volume.",
        likely_cause="A single API key is driving the spike.",
        recommended_action="Temporarily throttle the API key.",
        confidence="medium",
    )


def _alert_config(
    *,
    slack_webhook_url: str | None = "https://hooks.slack.com/services/T000/B000/FAKE",
    dry_run: bool = False,
    slack_channel_override: str | None = None,
    slack_max_retries: int = 3,
    slack_backoff_base_seconds: float = 0.5,
    slack_backoff_max_seconds: float = 8.0,
    slack_total_wait_cap_seconds: float = 15.0,
    slack_timeout_seconds: float = 5.0,
) -> AlertConfig:
    """Build an AlertConfig with controllable Slack settings."""
    return AlertConfig(
        slack_webhook_url=slack_webhook_url,
        dry_run=dry_run,
        slack_channel_override=slack_channel_override,
        slack_max_retries=slack_max_retries,
        slack_backoff_base_seconds=slack_backoff_base_seconds,
        slack_backoff_max_seconds=slack_backoff_max_seconds,
        slack_total_wait_cap_seconds=slack_total_wait_cap_seconds,
        slack_timeout_seconds=slack_timeout_seconds,
    )


def _make_fake_response(body: bytes = b"ok", status: int = 200) -> MagicMock:
    """Return a mock that mimics a urllib response context manager.

    Mirrors the pattern from tests/test_summarizer.py exactly, extended with
    a .getcode() method (Slack webhook returns the HTTP status code).
    """
    fake = MagicMock()
    fake.read.return_value = body
    fake.getcode.return_value = status
    fake.__enter__ = lambda s: s
    fake.__exit__ = MagicMock(return_value=False)
    return fake


# ---------------------------------------------------------------------------
# §8.1 — formatting tests (pure, no network)
# ---------------------------------------------------------------------------


def test_format_alert_block_kit_structure():
    """Returned dict must have blocks (4 items), attachments (1 item), text (str)."""
    payload = format_alert(_summary(), _anomaly())

    assert isinstance(payload, dict)

    # Top-level keys
    assert "blocks" in payload
    assert "attachments" in payload
    assert "text" in payload

    # blocks: exactly 4 items in order header, context, section, section
    blocks = payload["blocks"]
    assert isinstance(blocks, list)
    assert len(blocks) == 4
    assert blocks[0]["type"] == "header"
    assert blocks[1]["type"] == "context"
    assert blocks[2]["type"] == "section"
    assert blocks[3]["type"] == "section"

    # attachments: exactly 1 item
    attachments = payload["attachments"]
    assert isinstance(attachments, list)
    assert len(attachments) == 1

    # text: a non-empty string
    assert isinstance(payload["text"], str)
    assert len(payload["text"]) > 0


@pytest.mark.parametrize(
    "severity, expected_emoji",
    [
        ("low", ":information_source:"),
        ("medium", ":warning:"),
        ("high", ":rotating_light:"),
        ("critical", ":fire:"),
    ],
)
def test_format_alert_emoji_per_severity(severity: str, expected_emoji: str):
    """Header text must start with the correct severity emoji."""
    payload = format_alert(_summary(severity=severity), _anomaly(severity=severity))

    header_block = payload["blocks"][0]
    header_text = header_block["text"]["text"]
    assert header_text.startswith(expected_emoji), (
        f"Expected header to start with '{expected_emoji}' for severity '{severity}', "
        f"got: '{header_text}'"
    )


def test_format_alert_evidence_bullets_truncated():
    """When evidence notes exceed 5 items, only 5 bullets appear; no raw Event objects."""
    many_notes = [f"note_{i}" for i in range(10)]
    anomaly = _anomaly(notes=many_notes)
    payload = format_alert(_summary(), anomaly)

    evidence_block = payload["blocks"][2]
    evidence_text = evidence_block["text"]["text"]

    # Count bullet characters
    bullet_count = evidence_text.count("•")
    assert bullet_count <= 5, f"Expected ≤5 bullets, found {bullet_count}: {evidence_text!r}"

    # No raw Event objects — check that "Event(" substring is absent
    assert "Event(" not in evidence_text


def test_format_alert_omits_channel_when_no_override():
    """No 'channel' key when slack_channel_override is None; key present when set."""
    config_no_override = _alert_config(slack_channel_override=None)
    config_with_override = _alert_config(slack_channel_override="#alerts-critical")

    payload_no_channel = format_alert(_summary(), _anomaly())
    # format_alert is a pure function — it receives summary + anomaly only.
    # The channel override must be applied by SlackAlerter.send or by passing config.
    # Per plan §5.1: "channel" only present if slack_channel_override set.
    # We test via SlackAlerter to pick up the config-driven channel injection.
    alerter_no_override = SlackAlerter(config_no_override, sleep=MagicMock())
    alerter_with_override = SlackAlerter(config_with_override, sleep=MagicMock())

    # Build payload directly via format_alert to check pure shape
    pure_payload = format_alert(_summary(), _anomaly())
    assert "channel" not in pure_payload

    # When channel override is active, the delivered payload should contain "channel"
    # We verify this via dry_run so no network is touched.
    dry_config_with_channel = _alert_config(dry_run=True, slack_channel_override="#alerts-critical")
    alerter_dry_channel = SlackAlerter(dry_config_with_channel, sleep=MagicMock())
    result = alerter_dry_channel.send(_summary(), _anomaly())
    assert "channel" in result.payload

    dry_config_no_channel = _alert_config(dry_run=True, slack_channel_override=None)
    alerter_dry_no_channel = SlackAlerter(dry_config_no_channel, sleep=MagicMock())
    result_no_channel = alerter_dry_no_channel.send(_summary(), _anomaly())
    assert "channel" not in result_no_channel.payload


def test_format_alert_is_pure_deterministic():
    """Two calls with identical inputs must produce equal dicts (no clock, no RNG)."""
    summary = _summary()
    anomaly = _anomaly()

    first = format_alert(summary, anomaly)
    second = format_alert(summary, anomaly)

    assert first == second


# ---------------------------------------------------------------------------
# §8.1 — delivery tests (dry-run, missing config, network paths)
# ---------------------------------------------------------------------------


def test_send_dry_run_returns_payload_no_network():
    """dry_run=True: urlopen never called; DeliveryResult has delivered=True, dry_run=True."""
    config = _alert_config(dry_run=True)
    mock_sleep = MagicMock()

    with patch("opsmitra.slack_alerter.urlopen") as mock_urlopen:
        alerter = SlackAlerter(config, sleep=mock_sleep)
        result = alerter.send(_summary(), _anomaly())

    mock_urlopen.assert_not_called()

    assert isinstance(result, DeliveryResult)
    assert result.delivered is True
    assert result.dry_run is True

    expected_payload = format_alert(_summary(), _anomaly())
    assert result.payload == expected_payload


def test_send_missing_webhook_raises_configuration_error():
    """dry_run=False with slack_webhook_url=None raises SlackConfigurationError."""
    config = _alert_config(dry_run=False, slack_webhook_url=None)
    alerter = SlackAlerter(config, sleep=MagicMock())

    with pytest.raises(SlackConfigurationError) as exc_info:
        alerter.send(_summary(), _anomaly())

    # The error message must not contain any URL fragment
    error_message = str(exc_info.value)
    assert "https://" not in error_message
    assert "hooks.slack.com" not in error_message


def test_send_success_returns_ok_result():
    """Successful 200 response: delivered=True, attempts=1, status_code=200."""
    config = _alert_config(dry_run=False)
    mock_sleep = MagicMock()
    fake_response = _make_fake_response(body=b"ok", status=200)

    with patch("opsmitra.slack_alerter.urlopen", return_value=fake_response):
        alerter = SlackAlerter(config, sleep=mock_sleep)
        result = alerter.send(_summary(), _anomaly())

    assert isinstance(result, DeliveryResult)
    assert result.delivered is True
    assert result.attempts == 1
    assert result.status_code == 200
    assert result.dry_run is False


def test_send_retries_on_5xx_then_succeeds():
    """HTTPError 502 twice then 200: attempts==3, sleep called with [0.5, 1.0]."""
    config = _alert_config(dry_run=False, slack_max_retries=3)
    mock_sleep = MagicMock()
    fake_response = _make_fake_response(body=b"ok", status=200)

    http_error_502 = HTTPError(
        url="https://hooks.slack.com/services/T000/B000/FAKE",
        code=502,
        msg="Bad Gateway",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )

    side_effects = [http_error_502, http_error_502, fake_response]

    with patch("opsmitra.slack_alerter.urlopen", side_effect=side_effects):
        alerter = SlackAlerter(config, sleep=mock_sleep)
        result = alerter.send(_summary(), _anomaly())

    assert result.delivered is True
    assert result.attempts == 3
    # Backoff sleeps: before attempt 2 = 0.5s, before attempt 3 = 1.0s
    assert mock_sleep.call_args_list == [call(0.5), call(1.0)]


def test_send_does_not_retry_on_4xx():
    """HTTPError 403: attempts==1, sleep never called, delivered=False, status_code=403."""
    config = _alert_config(dry_run=False)
    mock_sleep = MagicMock()

    http_error_403 = HTTPError(
        url="https://hooks.slack.com/services/T000/B000/FAKE",
        code=403,
        msg="Forbidden",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )

    with patch("opsmitra.slack_alerter.urlopen", side_effect=http_error_403):
        alerter = SlackAlerter(config, sleep=mock_sleep)
        result = alerter.send(_summary(), _anomaly())

    assert result.delivered is False
    assert result.attempts == 1
    assert result.status_code == 403
    mock_sleep.assert_not_called()


def test_send_retries_on_timeout_then_gives_up():
    """TimeoutError on every call: attempts == max_retries+1, delivered=False, status_code=None."""
    max_retries = 2
    config = _alert_config(dry_run=False, slack_max_retries=max_retries)
    mock_sleep = MagicMock()

    with patch("opsmitra.slack_alerter.urlopen", side_effect=TimeoutError("timed out")):
        alerter = SlackAlerter(config, sleep=mock_sleep)
        result = alerter.send(_summary(), _anomaly())

    assert result.delivered is False
    assert result.attempts == max_retries + 1
    assert result.status_code is None


def test_webhook_url_never_appears_in_logs_or_exceptions(caplog):
    """Sentinel URL must not appear in any log record or raised exception message."""
    sentinel_url = "https://hooks.slack.com/services/UNIQUE-SENTINEL-XYZ"

    http_error_500 = HTTPError(
        url=sentinel_url,
        code=500,
        msg="Internal Server Error",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )
    http_error_403 = HTTPError(
        url=sentinel_url,
        code=403,
        msg="Forbidden",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )
    url_error = URLError("connection refused")
    timeout_error = TimeoutError("timed out")

    failure_cases: list[tuple[str, Exception | None, bool]] = [
        # (label, side_effect, missing_url)
        ("5xx_exhausted", http_error_500, False),
        ("4xx", http_error_403, False),
        ("url_error", url_error, False),
        ("timeout", timeout_error, False),
        ("missing_config", None, True),  # no URL in config
    ]

    for label, side_effect, missing_url in failure_cases:
        # Isolate each sub-case so a leak is attributed to the correct path.
        caplog.clear()

        config = _alert_config(
            dry_run=False,
            slack_webhook_url=None if missing_url else sentinel_url,
            slack_max_retries=1,
        )
        alerter = SlackAlerter(config, sleep=MagicMock())

        with caplog.at_level(logging.DEBUG, logger="opsmitra.slack_alerter"):
            if missing_url:
                # Should raise SlackConfigurationError
                with pytest.raises(SlackConfigurationError) as exc_info:
                    alerter.send(_summary(), _anomaly())
                assert "UNIQUE-SENTINEL-XYZ" not in str(exc_info.value), (
                    f"[{label}] sentinel URL leaked in exception: {exc_info.value}"
                )
            else:
                assert side_effect is not None
                with patch(
                    "opsmitra.slack_alerter.urlopen",
                    side_effect=side_effect,
                ):
                    result = alerter.send(_summary(), _anomaly())
                    # Result must not be delivered (all these are failure paths)
                    assert result.delivered is False, f"[{label}] expected failure"

        # Per-sub-case: sentinel must not appear in this sub-case's log records.
        assert "UNIQUE-SENTINEL-XYZ" not in caplog.text, (
            f"[{label}] sentinel URL leaked into log records"
        )


# ---------------------------------------------------------------------------
# L3 — Direct unit tests for private helpers
# ---------------------------------------------------------------------------


def test_backoff_seconds_caps_at_max():
    """_backoff_seconds(attempt) must follow base*2^(attempt-1) capped at max."""
    config = _alert_config(
        slack_backoff_base_seconds=0.5,
        slack_backoff_max_seconds=8.0,
    )
    alerter = SlackAlerter(config, sleep=MagicMock())

    expected = [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]
    for attempt, exp in enumerate(expected, start=1):
        assert alerter._backoff_seconds(attempt) == exp, (
            f"attempt={attempt}: expected {exp}, got {alerter._backoff_seconds(attempt)}"
        )


def test_should_retry_unknown_exception_returns_false():
    """_should_retry with an unknown exception type must return False."""
    config = _alert_config()
    alerter = SlackAlerter(config, sleep=MagicMock())

    result = alerter._should_retry(ValueError("custom"))
    assert result is False


def test_redact_with_none_webhook_url_returns_text_unchanged():
    """_redact must return the input string unchanged when webhook URL is None."""
    config = _alert_config(slack_webhook_url=None, dry_run=True)
    alerter = SlackAlerter(config, sleep=MagicMock())

    text = "Failed to POST https://hooks.slack.com/services/SECRET"
    result = alerter._redact(text)
    assert result == text
