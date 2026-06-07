"""Cooldown tracking for OpsMitra anomaly alerts.

Prevents repeated alerts for the same anomaly pattern within a configurable
suppression window. Persistence uses a local JSON file (v0 backend).

Step 10 hardening note: For stateless Lambda deployments, swap the JSON file
backend for DynamoDB or Redis. The AnomalyCooldown interface is unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from opsmitra.models import Anomaly

# L3 (S8): fcntl advisory lock — POSIX only (not available on Windows).
_LOCK_SUPPORTED = not sys.platform.startswith("win")
if _LOCK_SUPPORTED:
    import fcntl  # type: ignore[import]

logger = logging.getLogger(__name__)

_COOLDOWN_VERSION = 1
_MAX_FILE_BYTES = 1_000_000  # 1 MB cap — matches _MAX_THRESHOLDS_FILE_BYTES precedent


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def fingerprint(anomaly: Anomaly) -> str:
    """Return a deterministic, secret-free fingerprint for an anomaly.

    Format: ``{type}|{tenant_id or 'unknown'}|{subject_canonical}``.

    Subject is serialized as sorted JSON to ensure stability across dict
    orderings. Evidence (which may contain PII) is explicitly excluded.

    Args:
        anomaly: The anomaly to fingerprint.

    Returns:
        A stable, deterministic string suitable for cooldown keying and logging.
    """
    tenant = anomaly.tenant_id or "unknown"

    subject = anomaly.subject
    if isinstance(subject, dict):
        subject_str = json.dumps(subject, sort_keys=True, separators=(",", ":"))
    elif isinstance(subject, str):
        subject_str = subject
    else:
        # Fallback for unexpected types — canonicalize via JSON
        subject_str = json.dumps(subject, sort_keys=True, separators=(",", ":"))

    return f"{anomaly.type}|{tenant}|{subject_str}"


# ---------------------------------------------------------------------------
# CooldownEntry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CooldownEntry:
    """An immutable record of when an anomaly fingerprint was last alerted."""

    fingerprint: str
    last_alerted: datetime  # tz-aware UTC


# ---------------------------------------------------------------------------
# AnomalyCooldown
# ---------------------------------------------------------------------------


class AnomalyCooldown:
    """In-memory cooldown store backed by a JSON file on disk.

    Args:
        path: Path to the JSON persistence file. May not exist yet.
        window_seconds: Suppression window in seconds. ``0`` means never suppress.
        clock: Injectable callable returning current UTC datetime (for testing).
        max_file_bytes: Maximum allowed serialised file size before a ValueError
                        is raised in ``persist()``.

    NOTE: single-host backend with a durable named lockfile at ``<path>.lock``.
    Concurrent processes on the same host are serialised by POSIX advisory
    ``flock``; a writer that loses the lock race logs a WARNING and skips persist
    (cooldown is performance, not correctness). For multi-host stateless
    deployments (e.g. Lambda), swap for DynamoDB/Redis — the ``AnomalyCooldown``
    interface is unchanged.
    """

    def __init__(
        self,
        path: Path | None,
        window_seconds: int,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_file_bytes: int = _MAX_FILE_BYTES,
    ) -> None:
        self._path = path
        self._window_seconds = window_seconds
        self._now = clock
        self._max_file_bytes = max_file_bytes
        # fingerprint -> CooldownEntry
        self._store: dict[str, CooldownEntry] = {}

        # Auto-load on construction if path exists
        if path is not None and Path(path).exists():
            self.load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_alert(self, anomaly: Anomaly, now: datetime | None = None) -> bool:
        """Return True if this anomaly should trigger an alert.

        Args:
            anomaly: The anomaly to check.
            now: Current time override (defaults to clock()).

        Returns:
            True if no entry exists or the entry has expired; False otherwise.
        """
        if now is not None and now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        current_time = now or self._now()
        fp = fingerprint(anomaly)
        entry = self._store.get(fp)
        if entry is None:
            return True
        elapsed = (current_time - entry.last_alerted).total_seconds()
        return elapsed >= self._window_seconds

    def record_alerted(self, anomaly: Anomaly, now: datetime | None = None) -> None:
        """Record that an alert was sent for this anomaly fingerprint.

        Args:
            anomaly: The anomaly that was alerted.
            now: Timestamp to record (defaults to clock()).
        """
        if now is not None and now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        current_time = now or self._now()
        fp = fingerprint(anomaly)
        self._store[fp] = CooldownEntry(fingerprint=fp, last_alerted=current_time)

    def prune_expired(self, now: datetime | None = None) -> int:
        """Remove entries whose suppression window has elapsed.

        Args:
            now: Current time override (defaults to clock()).

        Returns:
            Count of entries removed.
        """
        if now is not None and now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        current_time = now or self._now()
        expired = [
            fp
            for fp, entry in self._store.items()
            if (current_time - entry.last_alerted).total_seconds() >= self._window_seconds
        ]
        for fp in expired:
            del self._store[fp]
        return len(expired)

    def persist(self) -> None:
        """Write the current store to disk atomically via tempfile rename.

        Raises:
            ValueError: If the serialised data exceeds ``max_file_bytes``.
            OSError: On filesystem errors.
        """
        if self._path is None:
            return

        path = Path(self._path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {
            "version": _COOLDOWN_VERSION,
            "entries": [
                {
                    "fingerprint": entry.fingerprint,
                    "last_alerted": entry.last_alerted.isoformat(),
                }
                for entry in self._store.values()
            ],
        }
        serialised = json.dumps(data, indent=2)
        byte_size = len(serialised.encode("utf-8"))
        if byte_size > self._max_file_bytes:
            raise ValueError(
                f"cooldown store serialised size {byte_size} exceeds "
                f"max_file_bytes={self._max_file_bytes}"
            )

        # Atomic write: write to a temp file in the same directory then rename.
        # M2 (S10): acquire an exclusive advisory lock on a durable named lockfile
        # co-located with the cooldown JSON. Using a stable path (<path>.lock)
        # ensures two concurrent processes collide on the same lock file, giving
        # flock(LOCK_NB) real mutual exclusion. If the lock is held by another
        # process, skip persist gracefully — cooldown is perf, not correctness.
        # N4: flush + fsync before os.replace for cross-crash durability.
        dir_ = path.parent
        lock_path = str(path) + ".lock"
        lock_fd: int | None = None
        if _LOCK_SUPPORTED:
            try:
                lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logger.warning("cooldown persist skipped: another writer holds the lock")
                if lock_fd is not None:
                    try:
                        os.close(lock_fd)
                    except OSError:
                        pass
                return
        fd, tmp_path = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(serialised)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, str(path))
        except Exception:
            # Clean up the temp file if rename fails
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        finally:
            if lock_fd is not None:
                try:
                    os.close(lock_fd)
                except OSError:
                    pass

    def load(self) -> None:
        """Load the cooldown store from disk.

        Silently resets to an empty store on any parse/schema error and emits
        a WARNING — cooldown is a performance optimization, not a correctness
        requirement, so a corrupt file must never block alerting.

        Idempotent: safe to call multiple times; state is replaced, not merged.
        """
        if self._path is None:
            return

        path = Path(self._path)
        if not path.exists():
            return

        try:
            file_size = path.stat().st_size
            if file_size > self._max_file_bytes:
                logger.warning(
                    "cooldown file %s exceeds size limit (%d bytes); resetting store",
                    path,
                    file_size,
                )
                self._store = {}
                return

            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "cooldown file %s contains malformed JSON (%s); resetting store",
                path,
                exc,
            )
            self._store = {}
            return
        except OSError as exc:
            logger.warning(
                "cooldown file %s could not be read (%s); resetting store",
                path,
                exc,
            )
            self._store = {}
            return

        try:
            entries = data.get("entries", [])
            store: dict[str, CooldownEntry] = {}
            for raw in entries:
                fp = str(raw["fingerprint"])
                last_alerted_str = str(raw["last_alerted"])
                last_alerted = datetime.fromisoformat(last_alerted_str)
                if last_alerted.tzinfo is None:
                    last_alerted = last_alerted.replace(tzinfo=timezone.utc)
                store[fp] = CooldownEntry(fingerprint=fp, last_alerted=last_alerted)
            self._store = store
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "cooldown file %s has unexpected schema (%s); resetting store",
                path,
                exc,
            )
            self._store = {}
