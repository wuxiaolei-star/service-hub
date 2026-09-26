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


def _write_config(tmp_path: Path, extra: str) -> Path:
    config = tmp_path / "hub.yaml"
    config.write_text(
        "deployment:\n  mode: offline\n"
        "storage:\n  root: /var/lib/hub\n"
        "database:\n  url: sqlite:////var/lib/hub/db/hub.db\n"
        "uploads:\n  max_size_bytes: 1024\n"
        "runner:\n  shared_token: runner-test-secret\n" + extra,
        encoding="utf-8",
    )
    return config


def test_plugins_signature_node_defaults_to_current_behaviour(tmp_path: Path) -> None:
    """B9/G9: an absent plugins node means no verification, exactly as before."""
    settings = HubSettings.from_yaml(_write_config(tmp_path, ""))

    assert settings.plugins.signature.public_keys == []
    assert settings.plugins.signature.require_signed is False


def test_plugins_signature_accepts_public_keys_for_verification(tmp_path: Path) -> None:
    import base64

    key = base64.b64encode(bytes(range(32))).decode("ascii")
    settings = HubSettings.from_yaml(
        _write_config(
            tmp_path,
            "plugins:\n"
            "  signature:\n"
            f"    public_keys:\n      - \"{key}\"\n"
            "    require_signed: true\n",
        )
    )

    assert settings.plugins.signature.public_keys == [key]
    assert settings.plugins.signature.require_signed is True


def test_plugins_signature_requires_a_key_when_enforcing_signatures(tmp_path: Path) -> None:
    """require_signed with zero keys would reject every upload; refuse to boot."""
    config = _write_config(
        tmp_path, "plugins:\n  signature:\n    require_signed: true\n"
    )

    with pytest.raises(ValidationError, match="public_keys"):
        HubSettings.from_yaml(config)


@pytest.mark.parametrize(
    "entry",
    ["not-base64!!", "c2hvcnQ=", "[]"],
)
def test_plugins_signature_rejects_malformed_public_keys(tmp_path: Path, entry: str) -> None:
    """Typoed keys must fail at load time, not at the first verification."""
    config = _write_config(
        tmp_path,
        "plugins:\n"
        "  signature:\n"
        f"    public_keys:\n      - \"{entry}\"\n",
    )

    with pytest.raises(ValidationError):
        HubSettings.from_yaml(config)
