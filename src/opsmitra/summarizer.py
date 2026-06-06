"""Local model summarization with deterministic fallback for Anomaly records."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from opsmitra.models import Anomaly

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = {"low", "medium", "high", "critical"}
_VALID_CONFIDENCES = {"low", "medium", "high"}
_REQUIRED_FIELDS = ("title", "severity", "summary", "likely_cause", "recommended_action", "confidence")
_OLLAMA_GENERATE_PATH = "/api/generate"


@dataclass(frozen=True)
class AlertSummary:
    """Frozen alert summary for Slack delivery."""

    title: str
    severity: str
    summary: str
    likely_cause: str
    recommended_action: str
    confidence: str


@runtime_checkable
class ModelClient(Protocol):
    """Protocol for model clients that generate text completions."""

    def complete(self, prompt: str) -> str:
        """Return raw completion text from the model."""
        ...


class OllamaClient:
    """Ollama-compatible HTTP client for local model inference."""

    def __init__(self, model_name: str, endpoint_url: str, timeout_seconds: float) -> None:
        """Initialize Ollama client with model identity and endpoint.

        Args:
            model_name: Name of the model (e.g. "llama3.1:8b").
            endpoint_url: Base URL of Ollama endpoint (e.g. "http://localhost:11434").
            timeout_seconds: Hard timeout for model requests.
        """
        self.model_name = model_name
        self.endpoint_url = endpoint_url
        self.timeout_seconds = timeout_seconds

    def complete(self, prompt: str) -> str:
        """POST to /api/generate and return the response field.

        Args:
            prompt: Text prompt for the model.

        Returns:
            The "response" field from the Ollama JSON envelope.

        Raises:
            HTTPError: On non-2xx response.
            URLError: On connection errors.
            TimeoutError: On timeout.
            json.JSONDecodeError: If response is not valid JSON.
        """
        url = f"{self.endpoint_url.rstrip('/')}{_OLLAMA_GENERATE_PATH}"
        body = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urlopen(request, timeout=self.timeout_seconds) as response:
            raw_response = response.read().decode("utf-8")

        parsed = json.loads(raw_response)
        return str(parsed["response"])


class FallbackSummarizer:
    """Deterministic fallback summarizer (no model dependency)."""

    def summarize(self, anomaly: Anomaly) -> AlertSummary:
        """Generate a deterministic AlertSummary from anomaly data.

        Args:
            anomaly: The anomaly record to summarize.

        Returns:
            A deterministic AlertSummary.
        """
        tenant_id = anomaly.tenant_id if anomaly.tenant_id is not None else "unknown"
        window_start = anomaly.window_start.isoformat()
        window_end = anomaly.window_end.isoformat()

        title = f"{anomaly.severity.capitalize()} {anomaly.type} detected"
        summary = f"Tenant {tenant_id} triggered {anomaly.type} between {window_start} and {window_end}."
        notes = anomaly.evidence.get("notes") or []
        likely_cause = (
            str(notes[0])
            if notes
            else "Deterministic detector evidence — see anomaly record."
        )
        recommended_action = (
            anomaly.recommended_actions[0]
            if anomaly.recommended_actions
            else "Review anomaly details."
        )

        return AlertSummary(
            title=title,
            severity=anomaly.severity,
            summary=summary,
            likely_cause=likely_cause,
            recommended_action=recommended_action,
            confidence="low",
        )


class Summarizer:
    """Summarizer with model client and fallback on failure."""

    def __init__(self, client: ModelClient, fallback: FallbackSummarizer | None = None) -> None:
        """Initialize Summarizer with a ModelClient.

        Args:
            client: The ModelClient implementation (e.g. OllamaClient).
            fallback: Optional FallbackSummarizer to use on error. Defaults to a new instance.
        """
        self.client = client
        self._fallback = fallback if fallback is not None else FallbackSummarizer()

    def summarize(self, anomaly: Anomaly) -> AlertSummary:
        """Summarize an anomaly using the model client, falling back on failure.

        Args:
            anomaly: The anomaly to summarize.

        Returns:
            An AlertSummary, either from the model or from the fallback.
        """
        try:
            prompt = _build_prompt(anomaly)
            raw = self.client.complete(prompt)
            return _parse_and_validate(raw)
        except Exception as e:
            logger.warning(
                "Model summarization failed for anomaly %s; using fallback: %s",
                anomaly.id,
                type(e).__name__,
            )
            return self._fallback.summarize(anomaly)


def _build_prompt(anomaly: Anomaly) -> str:
    """Build a prompt for the model with scoped evidence (no raw logs).

    Args:
        anomaly: The anomaly to summarize.

    Returns:
        A prompt string that includes anomaly evidence and instructs JSON output.
    """
    scoped = _scoped_evidence(anomaly)

    return (
        "You are an SRE assistant. Summarize the anomaly below for a Slack alert.\n"
        "Respond with ONLY a JSON object containing exactly these keys:\n"
        "title, severity, summary, likely_cause, recommended_action, confidence.\n\n"
        "Rules:\n"
        "- severity must be one of: low, medium, high, critical.\n"
        "- confidence must be one of: low, medium, high.\n"
        "- Keep summary under 240 characters.\n"
        "- Do not invent facts beyond the evidence shown.\n\n"
        "Anomaly evidence (scoped — no raw log payloads):\n"
        f"{json.dumps(scoped, indent=2)}"
    )


def _scoped_evidence(anomaly: Anomaly) -> dict[str, Any]:
    """Extract whitelisted evidence fields from anomaly.

    Args:
        anomaly: The anomaly record.

    Returns:
        A dict with only the scoped evidence fields (type, severity, etc.).
    """
    return {
        "type": anomaly.type,
        "severity": anomaly.severity,
        "tenant_id": anomaly.tenant_id,
        "subject": anomaly.subject,
        "observed": anomaly.observed,
        "baseline": anomaly.baseline,
        "ratio": anomaly.ratio,
        "evidence": anomaly.evidence,
        "recommended_actions": anomaly.recommended_actions,
    }


def _parse_and_validate(raw: str) -> AlertSummary:
    """Parse and validate model JSON output.

    Args:
        raw: Raw JSON string from the model.

    Returns:
        A validated AlertSummary.

    Raises:
        ValueError: If JSON is malformed, missing required fields, or has invalid enum values.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object, not other type")

    # Check required fields
    for field_name in _REQUIRED_FIELDS:
        if field_name not in parsed:
            raise ValueError(f"Missing required field: {field_name}")
        if not isinstance(parsed[field_name], str) or not parsed[field_name]:
            raise ValueError(f"Field '{field_name}' must be a non-empty string")

    # Validate severity enum
    severity = parsed["severity"]
    if severity not in _VALID_SEVERITIES:
        raise ValueError(f"severity '{severity}' not in {sorted(_VALID_SEVERITIES)}")

    # Validate confidence enum
    confidence = parsed["confidence"]
    if confidence not in _VALID_CONFIDENCES:
        raise ValueError(f"confidence '{confidence}' not in {sorted(_VALID_CONFIDENCES)}")

    # Soft cap on summary length
    summary = parsed["summary"]
    if len(summary) > 480:
        raise ValueError(f"summary length {len(summary)} exceeds limit of 480")

    return AlertSummary(
        title=parsed["title"],
        severity=severity,
        summary=summary,
        likely_cause=parsed["likely_cause"],
        recommended_action=parsed["recommended_action"],
        confidence=confidence,
    )
