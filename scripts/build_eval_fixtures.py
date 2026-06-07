"""Build evaluation fixture files for OpsMitra evaluation harness.

Generates deterministic fixture pairs (*.events.jsonl + *.expected.json) under
tests/fixtures/evaluation/. Running this script twice produces byte-identical
output.

Usage:
    python scripts/build_eval_fixtures.py [--output-dir tests/fixtures/evaluation]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from repo root without installing the package
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from opsmitra.generator import Incident, SyntheticLogGenerator, write_jsonl  # noqa: E402
from opsmitra.detectors import detect_anomalies  # noqa: E402


def _fmt(dt: datetime) -> str:
    """Format a UTC datetime as ISO 8601 with Z suffix."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Fixture 1: sms-abuse-baseline
# ---------------------------------------------------------------------------

def build_sms_abuse_baseline(output_dir: Path) -> None:
    """Single SMS abuse incident on tenant_acme — must_detect=true."""
    window_start = datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    window_end = datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc)
    baseline_start = window_start - timedelta(hours=1)

    incident = Incident(
        kind="sms_abuse",
        tenant_id="tenant_acme",
        start=window_start + timedelta(minutes=5),
        duration_minutes=30,
        volume=600,
        api_key_id="key_acme_abused",
    )

    gen = SyntheticLogGenerator(seed=42)
    events = gen.generate(start=baseline_start, hours=2, incidents=[incident])

    # Verify detector fires as expected
    anomalies = detect_anomalies(events, window_start, window_end)
    sms_anomalies = [a for a in anomalies if a.type == "sms_abuse_spike"]
    assert len(sms_anomalies) == 1, f"Expected 1 sms_abuse_spike, got {len(sms_anomalies)}"
    a = sms_anomalies[0]
    assert a.tenant_id == "tenant_acme"

    # Write events — ONLY the window events (no baseline needed in file;
    # detect_anomalies handles empty baseline gracefully; ratio still fires
    # because observed_count >= sms_min_count=500 means ratio uses fallback)
    # BUT: to preserve the ratio defense, write ALL events (baseline + window).
    events_path = output_dir / "sms-abuse-baseline.events.jsonl"
    write_jsonl(events, events_path)

    expected = {
        "name": "sms-abuse-baseline",
        "description": "Single SMS abuse incident on tenant_acme over a 60-minute window.",
        "window": {
            "start": _fmt(window_start),
            "end": _fmt(window_end),
        },
        "expected_incidents": [
            {
                "type": "sms_abuse_spike",
                "tenant_id": "tenant_acme",
                "subject": {"api_key_id": "key_acme_abused", "endpoint": "/sms/send"},
                "severity": "high",
                "first_seen": _fmt(window_start + timedelta(minutes=5)),
                "must_detect": True,
            }
        ],
        "thresholds": None,
    }

    expected_path = output_dir / "sms-abuse-baseline.expected.json"
    expected_path.write_text(json.dumps(expected, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"[OK] sms-abuse-baseline: {len(events)} events, 1 expected incident")


# ---------------------------------------------------------------------------
# Fixture 2: auth-burst-baseline
# ---------------------------------------------------------------------------

def build_auth_burst_baseline(output_dir: Path) -> None:
    """Single auth failure burst on tenant_beta — must_detect=true."""
    window_start = datetime(2026, 1, 15, 2, 0, 0, tzinfo=timezone.utc)
    window_end = datetime(2026, 1, 15, 3, 0, 0, tzinfo=timezone.utc)
    baseline_start = window_start - timedelta(hours=1)

    incident = Incident(
        kind="auth_failure_burst",
        tenant_id="tenant_beta",
        start=window_start + timedelta(minutes=10),
        duration_minutes=30,
        volume=500,
        ip="198.51.100.99",
    )

    gen = SyntheticLogGenerator(seed=43)
    events = gen.generate(start=baseline_start, hours=2, incidents=[incident])

    # Verify detector fires
    anomalies = detect_anomalies(events, window_start, window_end)
    auth_anomalies = [a for a in anomalies if a.type == "auth_failure_burst"]
    assert len(auth_anomalies) == 1, f"Expected 1 auth_failure_burst, got {len(auth_anomalies)}"
    a = auth_anomalies[0]
    assert a.subject["ip"] == "198.51.100.99"

    events_path = output_dir / "auth-burst-baseline.events.jsonl"
    write_jsonl(events, events_path)

    expected = {
        "name": "auth-burst-baseline",
        "description": "Single auth failure burst from IP 198.51.100.99 on tenant_beta over a 60-minute window.",
        "window": {
            "start": _fmt(window_start),
            "end": _fmt(window_end),
        },
        "expected_incidents": [
            {
                "type": "auth_failure_burst",
                "tenant_id": "*",
                "subject": {"ip": "198.51.100.99", "endpoint": "/auth/login"},
                "severity": "high",
                "first_seen": _fmt(window_start + timedelta(minutes=10)),
                "must_detect": True,
            }
        ],
        "thresholds": None,
    }

    expected_path = output_dir / "auth-burst-baseline.expected.json"
    expected_path.write_text(json.dumps(expected, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"[OK] auth-burst-baseline: {len(events)} events, 1 expected incident")


# ---------------------------------------------------------------------------
# Fixture 3: noise-only
# ---------------------------------------------------------------------------

def build_noise_only(output_dir: Path) -> None:
    """Normal traffic only — 0 expected anomalies. FP regression guard."""
    window_start = datetime(2026, 1, 15, 4, 0, 0, tzinfo=timezone.utc)
    window_end = datetime(2026, 1, 15, 5, 0, 0, tzinfo=timezone.utc)
    baseline_start = window_start - timedelta(hours=1)

    gen = SyntheticLogGenerator(seed=7, tenants=("tenant_acme", "tenant_beta", "tenant_cedar"))
    events = gen.generate(start=baseline_start, hours=2)

    # Verify no anomalies detected
    anomalies = detect_anomalies(events, window_start, window_end)
    assert len(anomalies) == 0, (
        f"Expected 0 anomalies for noise-only, got {len(anomalies)}: "
        + ", ".join(f"{a.type}@{a.tenant_id}" for a in anomalies)
    )

    events_path = output_dir / "noise-only.events.jsonl"
    write_jsonl(events, events_path)

    expected = {
        "name": "noise-only",
        "description": "Normal baseline traffic across 3 tenants — no incidents injected. FP regression guard.",
        "window": {
            "start": _fmt(window_start),
            "end": _fmt(window_end),
        },
        "expected_incidents": [],
        "thresholds": None,
    }

    expected_path = output_dir / "noise-only.expected.json"
    expected_path.write_text(json.dumps(expected, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"[OK] noise-only: {len(events)} events, 0 expected incidents")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Build evaluation fixture files.")
    parser.add_argument(
        "--output-dir",
        default="tests/fixtures/evaluation",
        help="Output directory for fixture pairs (default: tests/fixtures/evaluation)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Writing fixtures to {output_dir.resolve()}")
    build_sms_abuse_baseline(output_dir)
    build_auth_burst_baseline(output_dir)
    build_noise_only(output_dir)
    print("Done.")


if __name__ == "__main__":
    main()
