"""Quota and retention settings contract tests for V2.1."""

from __future__ import annotations

import pytest
from hub_server.settings import (
    AuthSettings,
    HubSettings,
    QuotasSettings,
    RetentionSettings,
)

FULL_CONFIG = (
    "deployment:\n  mode: offline\n"
    "storage:\n  root: /tmp/data\n"
    "database:\n  url: sqlite:////tmp/hub.db\n"
    "uploads:\n  max_size_bytes: 1024\n"
    "runner:\n  shared_token: secret\n"
)


def test_quota_settings_defaults() -> None:
    settings = QuotasSettings()
    assert settings.enabled is True
    assert settings.max_total_bytes == 1024**4
    assert settings.max_file_count == 10000
    assert settings.max_concurrent_jobs == 8


def test_quota_settings_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError):
        QuotasSettings(max_total_bytes=0)
    with pytest.raises(ValueError):
        QuotasSettings(max_file_count=-1)


def test_retention_settings_defaults() -> None:
    settings = RetentionSettings()
    assert settings.input_ttl_hours == 720
    assert settings.sweep_interval_minutes == 30
    assert settings.backup_keep == 3


def test_hub_settings_embeds_quotas_and_retention(tmp_path) -> None:
    config = tmp_path / "hub.yaml"
    config.write_text(
        FULL_CONFIG
        + "quotas:\n  enabled: false\n  max_total_bytes: 2048\n"
        + "retention:\n  input_ttl_hours: 48\n",
        encoding="utf-8",
    )
    settings = HubSettings.from_yaml(config)
    assert settings.quotas.enabled is False
    assert settings.quotas.max_total_bytes == 2048
    assert settings.retention.input_ttl_hours == 48


def test_hub_settings_quota_defaults_without_sections(tmp_path) -> None:
    config = tmp_path / "hub.yaml"
    config.write_text(FULL_CONFIG, encoding="utf-8")
    settings = HubSettings.from_yaml(config)
    assert settings.quotas.enabled is True
    assert settings.retention.input_ttl_hours == 720


def test_hub_settings_rejects_unknown_quota_keys(tmp_path) -> None:
    config = tmp_path / "hub.yaml"
    config.write_text(FULL_CONFIG + "quotas:\n  bogus: 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        HubSettings.from_yaml(config)


def test_auth_import_still_available() -> None:
    assert AuthSettings().mode == "required"
