from pathlib import Path

import pytest
from hub_server.settings import HubSettings
from pydantic import ValidationError


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
