"""RED tests for DetectorThresholds dataclass and resolve_thresholds function.

All imports are expected to fail at collection time (RED state).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# RED: these imports fail until DetectorThresholds + resolve_thresholds are added to config.py
from opsmitra.config import DetectorThresholds, resolve_thresholds  # noqa: E402

# ---------------------------------------------------------------------------
# Default field values (plan §6)
# ---------------------------------------------------------------------------


def test_detector_thresholds_default_sms_rate():
    """Default sms_abuse_rate_per_minute must be 5.0 per plan §6."""
    t = DetectorThresholds()
    assert t.sms_abuse_rate_per_minute == 5.0


def test_detector_thresholds_default_auth_burst_count():
    """Default auth_burst_count must be 10 per plan §6."""
    t = DetectorThresholds()
    assert t.auth_burst_count == 10


def test_detector_thresholds_default_auth_burst_window():
    """Default auth_burst_window_seconds must be 60 per plan §6."""
    t = DetectorThresholds()
    assert t.auth_burst_window_seconds == 60


def test_detector_thresholds_default_endpoint_error_rate():
    """Default endpoint_error_rate must be 0.10 per plan §6."""
    t = DetectorThresholds()
    assert t.endpoint_error_rate == 0.10


def test_detector_thresholds_default_endpoint_error_min_samples():
    """Default endpoint_error_min_samples must be 20 per plan §6."""
    t = DetectorThresholds()
    assert t.endpoint_error_min_samples == 20


def test_detector_thresholds_default_cost_runaway_delta():
    """Default cost_runaway_delta_usd must be 10.0 per plan §6."""
    t = DetectorThresholds()
    assert t.cost_runaway_delta_usd == 10.0


def test_detector_thresholds_default_cost_runaway_window():
    """Default cost_runaway_window_seconds must be 300 per plan §6."""
    t = DetectorThresholds()
    assert t.cost_runaway_window_seconds == 300


def test_detector_thresholds_is_immutable():
    """DetectorThresholds must be frozen (immutable dataclass)."""
    t = DetectorThresholds()
    with pytest.raises((AttributeError, TypeError)):
        t.sms_abuse_rate_per_minute = 99.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# resolve_thresholds — precedence: endpoint > tenant > default
# ---------------------------------------------------------------------------


def test_resolve_thresholds_no_overrides_returns_defaults():
    """With no overrides, resolve_thresholds must return the default DetectorThresholds."""
    result = resolve_thresholds(overrides=None, tenant=None, endpoint=None)

    assert result == DetectorThresholds()


def test_resolve_thresholds_override_one_field():
    """A default-level override for one field must change that field only."""
    overrides = {"default": {"sms_abuse_rate_per_minute": 99.0}}
    result = resolve_thresholds(overrides=overrides, tenant=None, endpoint=None)

    assert result.sms_abuse_rate_per_minute == 99.0
    # All other fields unchanged
    assert result.auth_burst_count == DetectorThresholds().auth_burst_count


def test_resolve_thresholds_tenant_override_wins_over_default():
    """A tenant-specific override must beat the default-level override."""
    overrides = {
        "default": {"auth_burst_count": 5},
        "tenants": {"tenant_acme": {"auth_burst_count": 25}},
    }
    result = resolve_thresholds(overrides=overrides, tenant="tenant_acme", endpoint=None)

    assert result.auth_burst_count == 25


def test_resolve_thresholds_endpoint_wins_over_tenant():
    """An endpoint-level override must beat both tenant and default overrides.

    Plan §6 precedence: endpoint > tenant > default.
    """
    overrides = {
        "default": {"sms_abuse_rate_per_minute": 5.0},
        "tenants": {"tenant_acme": {"sms_abuse_rate_per_minute": 7.0}},
        "endpoints": {"/sms/send": {"sms_abuse_rate_per_minute": 4.0}},
    }
    result = resolve_thresholds(overrides=overrides, tenant="tenant_acme", endpoint="/sms/send")

    assert result.sms_abuse_rate_per_minute == 4.0


def test_resolve_thresholds_unknown_tenant_falls_back_to_default():
    """An unknown tenant must fall back to the default — not raise."""
    overrides = {
        "tenants": {"tenant_known": {"auth_burst_count": 50}},
    }
    result = resolve_thresholds(overrides=overrides, tenant="tenant_unknown", endpoint=None)

    assert result.auth_burst_count == DetectorThresholds().auth_burst_count


def test_resolve_thresholds_unknown_endpoint_falls_back_to_tenant():
    """An unknown endpoint must fall back to tenant override, not raise."""
    overrides = {
        "default": {"auth_burst_count": 10},
        "tenants": {"tenant_acme": {"auth_burst_count": 20}},
        "endpoints": {"/other/endpoint": {"auth_burst_count": 30}},
    }
    result = resolve_thresholds(overrides=overrides, tenant="tenant_acme", endpoint="/unknown/endpoint")

    assert result.auth_burst_count == 20


def test_resolve_thresholds_partial_tenant_preserves_defaults():
    """Tenant override of one field must leave all other fields at their defaults."""
    overrides = {"tenants": {"tenant_acme": {"auth_burst_count": 99}}}
    result = resolve_thresholds(overrides=overrides, tenant="tenant_acme", endpoint=None)

    assert result.auth_burst_count == 99
    assert result.sms_abuse_rate_per_minute == DetectorThresholds().sms_abuse_rate_per_minute
    assert result.endpoint_error_rate == DetectorThresholds().endpoint_error_rate


# ---------------------------------------------------------------------------
# Load from OPSMITRA_THRESHOLDS_PATH JSON file
# ---------------------------------------------------------------------------


def test_load_thresholds_from_json_file(tmp_path: Path):
    """Reading a valid JSON thresholds file and resolving must honour its values."""
    from opsmitra.config import load_thresholds

    thresholds_file = tmp_path / "thresholds.json"
    payload = {
        "default": {"sms_abuse_rate_per_minute": 6.0},
        "tenants": {
            "tenant_acme": {"auth_burst_count": 25}
        },
        "endpoints": {
            "/sms/send": {"sms_abuse_rate_per_minute": 4.0}
        },
    }
    thresholds_file.write_text(json.dumps(payload))

    overrides = load_thresholds(thresholds_file)
    result = resolve_thresholds(overrides=overrides, tenant=None, endpoint=None)
    assert result.sms_abuse_rate_per_minute == 6.0

    result_tenant = resolve_thresholds(overrides=overrides, tenant="tenant_acme", endpoint=None)
    assert result_tenant.auth_burst_count == 25

    result_endpoint = resolve_thresholds(overrides=overrides, tenant="tenant_acme", endpoint="/sms/send")
    assert result_endpoint.sms_abuse_rate_per_minute == 4.0


def test_load_thresholds_unknown_field_raises_value_error(tmp_path: Path):
    """An unknown field in the thresholds JSON must raise ValueError at load time.

    Plan §6: 'Unknown field names raise ValueError("unknown threshold field: <name>") at load time.'
    """
    from opsmitra.config import load_thresholds

    thresholds_file = tmp_path / "thresholds.json"
    thresholds_file.write_text(json.dumps({"default": {"nonexistent_threshold": 99.0}}))

    with pytest.raises(ValueError, match="unknown"):
        load_thresholds(thresholds_file)
