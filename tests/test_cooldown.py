"""RED-phase + Step 10 extension tests for opsmitra.cooldown.

All imports from opsmitra.cooldown are expected to fail at collection until
src/opsmitra/cooldown.py is implemented (Step 8, Phase A).

Mocking style mirrors tests/test_slack_alerter.py.
Clock injection: AnomalyCooldown accepts clock=Callable[[], datetime] so tests
can advance time deterministically without freezegun.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from opsmitra.models import Anomaly

# These imports are expected to fail (ImportError) until the module exists.
from opsmitra.cooldown import AnomalyCooldown, fingerprint  # noqa: E402


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)


def _anomaly(
    *,
    anomaly_type: str = "sms_abuse_spike",
    tenant_id: str | None = "tenant_acme",
    subject: dict | None = None,
    evidence: dict | None = None,
) -> Anomaly:
    return Anomaly(
        id="anom_test_1",
        type=anomaly_type,
        severity="high",
        window_start=datetime(2026, 6, 7, 11, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
        tenant_id=tenant_id,
        subject=subject if subject is not None else {"endpoint": "/sms/send", "api_key_id": "key_abc"},
        observed={"count": 600},
        baseline={"hourly_count": 40},
        ratio=15.0,
        evidence=evidence if evidence is not None else {"notes": ["unusual SMS volume"]},
        recommended_actions=["Throttle the API key"],
    )


# ---------------------------------------------------------------------------
# fingerprint() tests
# ---------------------------------------------------------------------------


def test_fingerprint_is_deterministic():
    """Same anomaly built twice must produce the same fingerprint string."""
    a1 = _anomaly()
    a2 = _anomaly()
    assert fingerprint(a1) == fingerprint(a2)


def test_fingerprint_differs_by_type():
    """Changing anomaly.type must produce a different fingerprint."""
    a1 = _anomaly(anomaly_type="sms_abuse_spike")
    a2 = _anomaly(anomaly_type="auth_failure_burst")
    assert fingerprint(a1) != fingerprint(a2)


def test_fingerprint_differs_by_tenant():
    """Changing tenant_id must produce a different fingerprint."""
    a1 = _anomaly(tenant_id="tenant_acme")
    a2 = _anomaly(tenant_id="tenant_beta")
    assert fingerprint(a1) != fingerprint(a2)


def test_fingerprint_handles_none_tenant():
    """When tenant_id is None the fingerprint string must contain 'unknown'."""
    a = _anomaly(tenant_id=None)
    fp = fingerprint(a)
    assert "unknown" in fp


def test_fingerprint_contains_no_secrets():
    """Evidence containing a webhook URL must NOT appear in the fingerprint string."""
    secret_url = "https://hooks.slack.com/services/AAA/BBB/SECRETTOKEN"
    a = _anomaly(
        evidence={
            "notes": ["unusual SMS volume"],
            "webhook": secret_url,
        }
    )
    fp = fingerprint(a)
    assert secret_url not in fp
    assert "hooks.slack.com" not in fp


# ---------------------------------------------------------------------------
# AnomalyCooldown.should_alert / record_alerted tests
# ---------------------------------------------------------------------------


def test_should_alert_true_for_first_sighting(tmp_path: Path):
    """A fresh cooldown store with no prior entries must return True for any anomaly."""
    cooldown = AnomalyCooldown(
        tmp_path / "cooldown.json",
        window_seconds=3600,
        clock=_now,
    )
    assert cooldown.should_alert(_anomaly()) is True


def test_should_alert_false_within_window(tmp_path: Path):
    """After record_alerted, an immediate should_alert call must return False."""
    now_time = _now()
    cooldown = AnomalyCooldown(
        tmp_path / "cooldown.json",
        window_seconds=3600,
        clock=lambda: now_time,
    )
    a = _anomaly()
    cooldown.record_alerted(a)
    assert cooldown.should_alert(a) is False


def test_should_alert_true_after_window_expires(tmp_path: Path):
    """After the cooldown window expires, should_alert must return True again."""
    now_time = _now()
    cooldown = AnomalyCooldown(
        tmp_path / "cooldown.json",
        window_seconds=3600,
        clock=lambda: now_time,
    )
    a = _anomaly()
    cooldown.record_alerted(a)

    # Advance time past the cooldown window
    future_time = now_time + timedelta(seconds=3601)
    assert cooldown.should_alert(a, now=future_time) is True


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


def test_cooldown_persists_to_disk(tmp_path: Path):
    """After persist(), a new AnomalyCooldown loaded from the same path must
    suppress the same fingerprint as if it had been alerted in this session."""
    cooldown_path = tmp_path / "cooldown.json"
    now_time = _now()

    cooldown1 = AnomalyCooldown(cooldown_path, window_seconds=3600, clock=lambda: now_time)
    a = _anomaly()
    cooldown1.record_alerted(a)
    cooldown1.persist()

    # Load fresh instance from disk
    cooldown2 = AnomalyCooldown(cooldown_path, window_seconds=3600, clock=lambda: now_time)
    cooldown2.load()
    assert cooldown2.should_alert(a) is False


def test_cooldown_silent_reset_on_malformed_file(tmp_path: Path, caplog):
    """Writing garbage JSON to the cooldown path must not raise; the instance
    behaves as a fresh store and emits a WARNING log."""
    cooldown_path = tmp_path / "cooldown.json"
    cooldown_path.write_text("NOT VALID JSON !!!")

    with caplog.at_level(logging.WARNING, logger="opsmitra.cooldown"):
        cooldown = AnomalyCooldown(cooldown_path, window_seconds=3600, clock=_now)
        cooldown.load()

    # Must not suppress anything (fresh state)
    assert cooldown.should_alert(_anomaly()) is True
    # Must have emitted at least one WARNING
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warning_records) >= 1


# ---------------------------------------------------------------------------
# prune_expired tests
# ---------------------------------------------------------------------------


def test_cooldown_prune_expired_removes_old_entries(tmp_path: Path):
    """prune_expired() must remove entries older than window_seconds and return
    the count of removed entries. After pruning the entries no longer suppress."""
    cooldown_path = tmp_path / "cooldown.json"
    now_time = _now()
    window_seconds = 3600

    cooldown = AnomalyCooldown(cooldown_path, window_seconds=window_seconds, clock=lambda: now_time)

    # Record 3 distinct anomalies
    anomalies = [
        _anomaly(anomaly_type=f"type_{i}", subject={"endpoint": f"/ep/{i}"})
        for i in range(3)
    ]
    for a in anomalies:
        cooldown.record_alerted(a)

    # Advance time past expiry and prune
    future_time = now_time + timedelta(seconds=window_seconds + 1)
    removed_count = cooldown.prune_expired(now=future_time)

    assert removed_count == 3
    # Entries must no longer suppress after pruning
    for a in anomalies:
        assert cooldown.should_alert(a, now=future_time) is True


# ---------------------------------------------------------------------------
# Edge-case / coverage-gap tests
# ---------------------------------------------------------------------------


def test_fingerprint_with_string_subject():
    """A string subject must be included verbatim in the fingerprint."""
    a = _anomaly(subject={"endpoint": "/sms/send"})
    # This exercises the dict branch; string subject exercises the elif branch
    class FakeAnomaly:
        type = "sms_abuse_spike"
        tenant_id = "t1"
        subject = "plain_string_subject"

    fp = fingerprint(FakeAnomaly())  # type: ignore[arg-type]
    assert "plain_string_subject" in fp


def test_persist_raises_on_size_exceeded(tmp_path: Path):
    """persist() must raise ValueError when serialised size exceeds max_file_bytes."""
    cooldown = AnomalyCooldown(
        tmp_path / "cooldown.json",
        window_seconds=3600,
        clock=_now,
        max_file_bytes=10,  # Very small cap
    )
    cooldown.record_alerted(_anomaly())
    with pytest.raises(ValueError, match="exceeds"):
        cooldown.persist()


def test_persist_with_none_path():
    """persist() with path=None must be a no-op (no exception)."""
    cooldown = AnomalyCooldown(None, window_seconds=3600, clock=_now)
    cooldown.record_alerted(_anomaly())
    cooldown.persist()  # must not raise


def test_load_with_none_path():
    """load() with path=None must be a no-op (no exception)."""
    cooldown = AnomalyCooldown(None, window_seconds=3600, clock=_now)
    cooldown.load()  # must not raise
    assert cooldown.should_alert(_anomaly()) is True


def test_load_with_nonexistent_path(tmp_path: Path):
    """load() pointing to a non-existent file must leave the store empty."""
    cooldown = AnomalyCooldown(
        tmp_path / "does_not_exist.json",
        window_seconds=3600,
        clock=_now,
    )
    cooldown.load()
    assert cooldown.should_alert(_anomaly()) is True


def test_load_file_too_large_resets_store(tmp_path: Path, caplog):
    """A cooldown file larger than max_file_bytes must trigger silent reset + WARNING."""
    cooldown_path = tmp_path / "cooldown.json"
    cooldown_path.write_bytes(b"x" * 20)  # tiny cap so this exceeds it

    with caplog.at_level(logging.WARNING, logger="opsmitra.cooldown"):
        cooldown = AnomalyCooldown(
            cooldown_path,
            window_seconds=3600,
            clock=_now,
            max_file_bytes=5,  # force oversized
        )
        cooldown.load()

    assert cooldown.should_alert(_anomaly()) is True
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warning_records) >= 1


def test_load_naive_datetime_gets_utc(tmp_path: Path):
    """An entry with a naive (no timezone) last_alerted datetime in the JSON file
    must be treated as UTC — it should suppress within the window."""
    cooldown_path = tmp_path / "cooldown.json"
    # Write a file with a naive ISO datetime (no +00:00)
    now_time = _now()
    # Use the same fingerprint as _anomaly() would produce
    from opsmitra.cooldown import fingerprint as fp_fn
    a = _anomaly()
    fp_str = fp_fn(a)
    import json as _json
    data = {
        "version": 1,
        "entries": [
            {
                "fingerprint": fp_str,
                "last_alerted": "2026-06-07T12:00:00",  # naive — no timezone
            }
        ],
    }
    cooldown_path.write_text(_json.dumps(data))

    cooldown = AnomalyCooldown(cooldown_path, window_seconds=3600, clock=lambda: now_time)
    # The naive datetime is normalised to UTC; since now == last_alerted, elapsed=0 < 3600
    assert cooldown.should_alert(a) is False


def test_load_oserror_resets_store(tmp_path: Path, caplog):
    """An OSError when reading the cooldown file must trigger silent reset + WARNING."""
    cooldown_path = tmp_path / "cooldown.json"
    cooldown_path.write_text('{"version": 1, "entries": []}')

    # Remove read permissions to force an OSError
    cooldown_path.chmod(0o000)
    try:
        with caplog.at_level(logging.WARNING, logger="opsmitra.cooldown"):
            cooldown = AnomalyCooldown(cooldown_path, window_seconds=3600, clock=_now)
            cooldown.load()
    finally:
        cooldown_path.chmod(0o644)

    assert cooldown.should_alert(_anomaly()) is True
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warning_records) >= 1


def test_load_schema_error_resets_store(tmp_path: Path, caplog):
    """JSON with unexpected schema (missing 'fingerprint' key) must trigger silent reset."""
    cooldown_path = tmp_path / "cooldown.json"
    cooldown_path.write_text(
        '{"version": 1, "entries": [{"bad_key": "value", "last_alerted": "2026-06-07T12:00:00+00:00"}]}'
    )

    with caplog.at_level(logging.WARNING, logger="opsmitra.cooldown"):
        cooldown = AnomalyCooldown(cooldown_path, window_seconds=3600, clock=_now)
        cooldown.load()

    assert cooldown.should_alert(_anomaly()) is True


# ---------------------------------------------------------------------------
# N2: tz-aware guards
# ---------------------------------------------------------------------------


def test_should_alert_rejects_naive_datetime(tmp_path: Path):
    """should_alert must raise ValueError if a naive (tz-unaware) datetime is passed."""
    cooldown = AnomalyCooldown(None, window_seconds=3600, clock=_now)
    naive = datetime(2026, 6, 7, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValueError, match="timezone-aware"):
        cooldown.should_alert(_anomaly(), now=naive)


def test_record_alerted_rejects_naive_datetime(tmp_path: Path):
    """record_alerted must raise ValueError if a naive (tz-unaware) datetime is passed."""
    cooldown = AnomalyCooldown(None, window_seconds=3600, clock=_now)
    naive = datetime(2026, 6, 7, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValueError, match="timezone-aware"):
        cooldown.record_alerted(_anomaly(), now=naive)


def test_prune_expired_rejects_naive_datetime(tmp_path: Path):
    """prune_expired must raise ValueError if a naive (tz-unaware) datetime is passed."""
    cooldown = AnomalyCooldown(None, window_seconds=3600, clock=_now)
    naive = datetime(2026, 6, 7, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValueError, match="timezone-aware"):
        cooldown.prune_expired(now=naive)


# ---------------------------------------------------------------------------
# Step 10 L3 (S8) — fcntl advisory lock (N4 + concurrent writer)
# ---------------------------------------------------------------------------


import sys
import threading
import os


@pytest.mark.skipif(sys.platform.startswith("win"), reason="fcntl not available on Windows")
def test_concurrent_writers_second_logs_warning_and_skips_persist(tmp_path: Path, caplog):
    """When a concurrent process holds an exclusive fcntl lock on the durable
    <cooldown_path>.lock file, a second persist() call must:
      1. Emit a WARNING log line (not raise).
      2. Leave the original cooldown file content unchanged.

    M2 (S10): lock target is now the stable named lockfile at <path>.lock,
    not a per-call tempfile, so this test pre-acquires the lock on that path
    directly — no monkeypatching needed.
    """
    import fcntl

    cooldown_path = tmp_path / "cooldown.json"
    lock_path = str(cooldown_path) + ".lock"
    now_time = _now()

    # Write an initial known state to the cooldown file
    cooldown1 = AnomalyCooldown(cooldown_path, window_seconds=3600, clock=lambda: now_time)
    a = _anomaly()
    cooldown1.record_alerted(a)
    cooldown1.persist()
    original_content = cooldown_path.read_text(encoding="utf-8")

    # Simulate a concurrent writer by acquiring LOCK_EX on <cooldown_path>.lock
    # directly. When the second persist() tries flock(LOCK_NB) on the same path
    # it must get BlockingIOError and skip without modifying the file.
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        cooldown2 = AnomalyCooldown(cooldown_path, window_seconds=3600, clock=lambda: now_time)
        cooldown2.record_alerted(a)

        with caplog.at_level(logging.WARNING, logger="opsmitra.cooldown"):
            cooldown2.persist()  # must log WARNING + skip, not raise

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    # The WARNING must have been emitted
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("lock" in r.message.lower() or "skip" in r.message.lower() for r in warning_records), (
        f"Expected a WARNING about lock/skip, got records: {[r.message for r in warning_records]}"
    )

    # The original file content must be untouched
    current_content = cooldown_path.read_text(encoding="utf-8")
    assert current_content == original_content, (
        "persist() under locked condition must not modify the cooldown file"
    )


@pytest.mark.skipif(sys.platform.startswith("win"), reason="os.fsync/fcntl not available on Windows")
def test_persist_calls_fsync_before_replace(tmp_path: Path, monkeypatch):
    """persist() must call os.fsync(fd) BEFORE os.replace() for durability (N4).

    RED until N4 adds fh.flush() + os.fsync(fh.fileno()) before os.replace in persist().
    """
    call_order: list[str] = []

    real_fsync = os.fsync
    real_replace = os.replace

    def tracking_fsync(fd):
        call_order.append("fsync")
        return real_fsync(fd)

    def tracking_replace(src, dst):
        call_order.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", tracking_fsync)
    monkeypatch.setattr(os, "replace", tracking_replace)

    cooldown = AnomalyCooldown(tmp_path / "cooldown.json", window_seconds=3600, clock=_now)
    cooldown.record_alerted(_anomaly())
    cooldown.persist()

    # RED: current persist() does not call os.fsync, so 'fsync' won't appear
    assert "fsync" in call_order, (
        f"persist() must call os.fsync before os.replace. Call order: {call_order}"
    )
    assert "replace" in call_order, (
        f"persist() must call os.replace. Call order: {call_order}"
    )
    fsync_pos = call_order.index("fsync")
    replace_pos = call_order.index("replace")
    assert fsync_pos < replace_pos, (
        f"os.fsync must be called BEFORE os.replace. "
        f"fsync at index {fsync_pos}, replace at index {replace_pos} in {call_order}"
    )
