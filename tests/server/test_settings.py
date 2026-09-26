from pathlib import Path

import pytest
from hub_server.settings import HubSettings
from pydantic import ValidationError


def test_shared_deployment_token_overrides_transferred_stale_config(monkeypatch) -> None:
    monkeypatch.setenv("HUB_RUNNER_TOKEN", "fresh-target-token")
    settings = HubSettings.from_yaml(Path("config/hub.yaml.example"))
    assert settings.runner.shared_token.get_secret_value() == "fresh-target-token"


def test_arm64_runtime_selects_native_platform(monkeypatch) -> None:
    monkeypatch.setattr("platform.machine", lambda: "aarch64")
    settings = HubSettings.from_yaml(Path("config/hub.yaml.example"))
    assert settings.platform_arch == "arm64"


def test_loads_settings_from_yaml(tmp_path: Path) -> None:
    """A valid YAML document produces typed nested settings."""
    config = tmp_path / "hub.yaml"
    config.write_text(
        "deployment:\n  mode: offline\n"
        "storage:\n  root: /var/lib/hub\n"
        "database:\n  url: sqlite:////var/lib/hub/db/hub.db\n"
        "uploads:\n  max_size_bytes: 1024\n"
        "runner:\n  shared_token: runner-test-secret\n  poll_interval_seconds: 1\n",
        encoding="utf-8",
    )

    settings = HubSettings.from_yaml(config)

    assert settings.storage.root == Path("/var/lib/hub")
    assert settings.uploads.max_size_bytes == 1024


def test_rejects_unknown_yaml_settings(tmp_path: Path) -> None:
    """Unexpected settings cannot be silently accepted."""
    config = tmp_path / "hub.yaml"
    config.write_text(
        "deployment:\n  mode: offline\n"
        "storage:\n  root: /var/lib/hub\n"
        "database:\n  url: sqlite:////var/lib/hub/db/hub.db\n"
        "uploads:\n  max_size_bytes: 1024\n"
        "runner:\n  shared_token: runner-test-secret\n  poll_interval_seconds: 1\n"
        "unknown: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        HubSettings.from_yaml(config)


def test_rejects_non_mapping_yaml_root(tmp_path: Path) -> None:
    """A YAML list is not a valid Hub configuration document."""
    config = tmp_path / "hub.yaml"
    config.write_text("- offline\n", encoding="utf-8")

    with pytest.raises(ValueError, match="根节点必须是对象"):
        HubSettings.from_yaml(config)


def test_runner_resource_limits_default_node_is_present(tmp_path: Path) -> None:
    """B7/G7: the platform default cap ships enabled with conservative values."""
    config = tmp_path / "hub.yaml"
    config.write_text(
        "deployment:\n  mode: offline\n"
        "storage:\n  root: /var/lib/hub\n"
        "database:\n  url: sqlite:////var/lib/hub/db/hub.db\n"
        "uploads:\n  max_size_bytes: 1024\n"
        "runner:\n  shared_token: runner-test-secret\n",
        encoding="utf-8",
    )

    settings = HubSettings.from_yaml(config)

    assert settings.runner.resource_limits is not None
    assert settings.runner.resource_limits.memory_mb == 2048
    assert settings.runner.resource_limits.cpus == 1.5


def test_runner_resource_limits_node_can_be_disabled(tmp_path: Path) -> None:
    """A null node opts the whole platform out of default caps (manifest still wins)."""
    config = tmp_path / "hub.yaml"
    config.write_text(
        "deployment:\n  mode: offline\n"
        "storage:\n  root: /var/lib/hub\n"
        "database:\n  url: sqlite:////var/lib/hub/db/hub.db\n"
        "uploads:\n  max_size_bytes: 1024\n"
        "runner:\n  shared_token: runner-test-secret\n  resource_limits: null\n",
        encoding="utf-8",
    )

    settings = HubSettings.from_yaml(config)

    assert settings.runner.resource_limits is None


def test_runner_resource_limits_accepts_explicit_values(tmp_path: Path) -> None:
    config = tmp_path / "hub.yaml"
    config.write_text(
        "deployment:\n  mode: offline\n"
        "storage:\n  root: /var/lib/hub\n"
        "database:\n  url: sqlite:////var/lib/hub/db/hub.db\n"
        "uploads:\n  max_size_bytes: 1024\n"
        "runner:\n  shared_token: runner-test-secret\n"
        "  resource_limits:\n    memory_mb: 4096\n    cpus: 2\n",
        encoding="utf-8",
    )

    settings = HubSettings.from_yaml(config)

    assert settings.runner.resource_limits is not None
    assert settings.runner.resource_limits.memory_mb == 4096
    assert settings.runner.resource_limits.cpus == 2.0


@pytest.mark.parametrize("field", ["memory_mb", "cpus"])
def test_runner_resource_limits_reject_non_positive_values(
    tmp_path: Path, field: str
) -> None:
    config = tmp_path / "hub.yaml"
    config.write_text(
        "deployment:\n  mode: offline\n"
        "storage:\n  root: /var/lib/hub\n"
        "database:\n  url: sqlite:////var/lib/hub/db/hub.db\n"
        "uploads:\n  max_size_bytes: 1024\n"
        "runner:\n  shared_token: runner-test-secret\n"
        f"  resource_limits:\n    {field}: 0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        HubSettings.from_yaml(config)
