import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from opsmitra.models import Anomaly
from opsmitra.summarizer import AlertSummary, FallbackSummarizer, ModelClient, OllamaClient, Summarizer


def _anomaly() -> Anomaly:
    return Anomaly(
        id="anom_1",
        type="sms_abuse_spike",
        severity="high",
        window_start=datetime(2026, 5, 28, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 28, 11, tzinfo=timezone.utc),
        tenant_id="tenant_acme",
        subject={"api_key_id": "key_abused", "endpoint": "/sms/send"},
        observed={"count": 600, "cost_units": 600.0},
        baseline={"hourly_count": 40},
        ratio=15.0,
        evidence={"sample_request_ids": ["req_1"], "notes": ["single API key generated unusual SMS volume"]},
        recommended_actions=["Temporarily throttle the API key"],
    )


class StubClient(ModelClient):
    def complete(self, prompt: str) -> str:
        assert "raw full logs" not in prompt.lower()
        assert "req_1" in prompt
        return (
            '{"title":"SMS spike detected","severity":"high","summary":"Tenant tenant_acme sent unusual SMS volume.",'
            '"likely_cause":"A single API key is driving the spike.","recommended_action":"Temporarily throttle the API key.",'
            '"confidence":"medium"}'
        )


class BrokenClient(ModelClient):
    def complete(self, prompt: str) -> str:
        return "not json"


def test_summarizer_validates_model_json_output():
    summary = Summarizer(StubClient()).summarize(_anomaly())

    assert isinstance(summary, AlertSummary)
    assert summary.title == "SMS spike detected"
    assert summary.severity == "high"
    assert summary.confidence == "medium"


def test_summarizer_falls_back_when_model_output_is_invalid():
    summary = Summarizer(BrokenClient()).summarize(_anomaly())

    assert summary.title == "High sms_abuse_spike detected"
    assert "tenant_acme" in summary.summary
    assert summary.confidence == "low"


def test_fallback_summarizer_is_deterministic():
    first = FallbackSummarizer().summarize(_anomaly())
    second = FallbackSummarizer().summarize(_anomaly())

    assert first == second
    assert first.recommended_action == "Temporarily throttle the API key"


# ---------------------------------------------------------------------------
# OllamaClient: HTTP payload and response parsing
# ---------------------------------------------------------------------------


def _make_fake_response(body: bytes) -> MagicMock:
    """Return a mock that mimics urllib response context manager."""
    fake = MagicMock()
    fake.read.return_value = body
    fake.__enter__ = lambda s: s
    fake.__exit__ = MagicMock(return_value=False)
    return fake


def test_ollama_client_posts_expected_payload_to_generate_endpoint():
    """OllamaClient must POST to /api/generate with the required JSON body fields."""
    fake_response = _make_fake_response(b'{"response": "hello"}')

    with patch("opsmitra.summarizer.urlopen", return_value=fake_response) as mock_urlopen:
        client = OllamaClient("test-model", "http://localhost:11434", timeout_seconds=1.0)
        client.complete("test prompt")

    assert mock_urlopen.called
    request = mock_urlopen.call_args[0][0]

    assert request.full_url.endswith("/api/generate")
    assert request.method == "POST"

    body = json.loads(request.data)
    assert body["model"] == "test-model"
    assert body["prompt"] == "test prompt"
    assert body["stream"] is False
    assert body["format"] == "json"


def test_ollama_client_returns_response_text():
    """OllamaClient.complete must return the 'response' field from the Ollama envelope."""
    fake_response = _make_fake_response(b'{"response": "hello"}')

    with patch("opsmitra.summarizer.urlopen", return_value=fake_response):
        client = OllamaClient("test-model", "http://localhost:11434", timeout_seconds=1.0)
        result = client.complete("test prompt")

    assert result == "hello"


# ---------------------------------------------------------------------------
# Summarizer: network and timeout fallback paths
# ---------------------------------------------------------------------------


def test_summarizer_falls_back_on_network_error():
    """When ModelClient raises URLError, Summarizer must return FallbackSummarizer output."""
    class NetworkErrorClient(ModelClient):
        def complete(self, prompt: str) -> str:
            raise URLError("connection refused")

    summary = Summarizer(NetworkErrorClient()).summarize(_anomaly())

    assert summary.title == "High sms_abuse_spike detected"
    assert summary.confidence == "low"


def test_summarizer_falls_back_on_timeout():
    """When ModelClient raises TimeoutError, Summarizer must return FallbackSummarizer output."""
    class TimeoutClient(ModelClient):
        def complete(self, prompt: str) -> str:
            raise TimeoutError("simulated timeout")

    summary = Summarizer(TimeoutClient()).summarize(_anomaly())

    assert summary.title == "High sms_abuse_spike detected"
    assert summary.confidence == "low"


# ---------------------------------------------------------------------------
# Summarizer: JSON validation fallback paths
# ---------------------------------------------------------------------------


def _make_stub_returning(json_dict: dict) -> ModelClient:
    class FixedClient(ModelClient):
        def complete(self, prompt: str) -> str:
            return json.dumps(json_dict)

    return FixedClient()


def test_summarizer_falls_back_on_missing_required_field():
    """When model JSON is missing 'title', Summarizer must fall back to deterministic output."""
    payload = {
        # "title" intentionally omitted
        "severity": "high",
        "summary": "Some summary.",
        "likely_cause": "A cause.",
        "recommended_action": "An action.",
        "confidence": "medium",
    }
    summary = Summarizer(_make_stub_returning(payload)).summarize(_anomaly())

    assert summary.title == "High sms_abuse_spike detected"
    assert summary.confidence == "low"


def test_summarizer_falls_back_on_invalid_severity_enum():
    """When model returns severity 'emergency' (not in valid set), Summarizer must fall back."""
    payload = {
        "title": "Some alert",
        "severity": "emergency",  # invalid — not in {low, medium, high, critical}
        "summary": "Some summary.",
        "likely_cause": "A cause.",
        "recommended_action": "An action.",
        "confidence": "medium",
    }
    summary = Summarizer(_make_stub_returning(payload)).summarize(_anomaly())

    assert summary.title == "High sms_abuse_spike detected"
    assert summary.confidence == "low"


# ---------------------------------------------------------------------------
# Prompt content: scoped evidence, no raw logs, JSON fields instructed
# ---------------------------------------------------------------------------


class RecordingClient(ModelClient):
    """Captures the prompt and returns a valid JSON response."""

    def __init__(self) -> None:
        self.captured_prompt: str = ""

    def complete(self, prompt: str) -> str:
        self.captured_prompt = prompt
        return (
            '{"title":"SMS spike detected","severity":"high","summary":"Tenant tenant_acme sent unusual SMS volume.",'
            '"likely_cause":"A single API key is driving the spike.","recommended_action":"Temporarily throttle the API key.",'
            '"confidence":"medium"}'
        )


def test_prompt_includes_scoped_evidence_only():
    """Prompt must contain anomaly type, tenant_id, evidence notes; must NOT contain 'raw full logs';
    must instruct model to return JSON with the 6 AlertSummary fields."""
    anomaly = _anomaly()
    recording = RecordingClient()

    Summarizer(recording).summarize(anomaly)

    prompt = recording.captured_prompt

    # Must include key anomaly identifiers
    assert anomaly.type in prompt, "prompt must contain the anomaly type"
    assert anomaly.tenant_id in prompt, "prompt must contain tenant_id"
    assert "single API key generated unusual SMS volume" in prompt, "prompt must contain evidence notes"

    # Must never include the literal phrase 'raw full logs'
    assert "raw full logs" not in prompt.lower(), "prompt must not contain 'raw full logs'"

    # Must instruct model on the 6 required JSON output fields
    for field in ("title", "severity", "summary", "likely_cause", "recommended_action", "confidence"):
        assert field in prompt, f"prompt must instruct model to return field '{field}'"
