from __future__ import annotations

import pytest
from hub_server.settings import AuthSettings, HubSettings


def test_auth_settings_defaults_to_required_mode() -> None:
    settings = AuthSettings()
    assert settings.mode == "required"
    assert settings.session_ttl_hours == 24


def test_auth_settings_defaults_throttle_the_fifth_failure() -> None:
    """Losing these defaults would let the login endpoint be brute forced."""
    settings = AuthSettings()
    assert settings.login_failure_threshold == 5
    assert settings.login_lockout_base_seconds == 30
    assert settings.login_lockout_max_seconds == 900


def test_auth_settings_reject_a_ceiling_below_the_first_lock() -> None:
    """A ceiling under the base delay would shorten later locks instead of capping them."""
    with pytest.raises(ValueError):
        AuthSettings(login_lockout_base_seconds=60, login_lockout_max_seconds=30)


def test_auth_settings_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        AuthSettings(mode="open")


FULL_CONFIG = (
    "deployment:\n  mode: offline\n"
    "storage:\n  root: /tmp/data\n"
    "database:\n  url: sqlite:////tmp/hub.db\n"
    "uploads:\n  max_size_bytes: 1024\n"
    "runner:\n  shared_token: secret\n"
)


def test_hub_settings_embeds_auth_section(tmp_path) -> None:
    config = tmp_path / "hub.yaml"
    config.write_text(
        FULL_CONFIG + "auth:\n  mode: off\n  session_ttl_hours: 8\n", encoding="utf-8"
    )
    settings = HubSettings.from_yaml(config)
    assert settings.auth.mode == "off"
    assert settings.auth.session_ttl_hours == 8


def test_hub_auth_mode_env_overrides_yaml(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "hub.yaml"
    config.write_text(FULL_CONFIG + "auth:\n  mode: off\n", encoding="utf-8")
    monkeypatch.setenv("HUB_AUTH_MODE", "required")
    settings = HubSettings.from_yaml(config)
    assert settings.auth.mode == "required"


def test_hub_settings_default_auth_mode_is_required(tmp_path) -> None:
    config = tmp_path / "hub.yaml"
    config.write_text(FULL_CONFIG, encoding="utf-8")
    settings = HubSettings.from_yaml(config)
    assert settings.auth.mode == "required"
    assert settings.auth.session_ttl_hours == 24
