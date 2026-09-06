import importlib
from pathlib import Path

import hub_server.main as main
import pytest


def test_asgi_app_loads_settings_from_hub_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The module-level ASGI app uses the YAML selected by HUB_CONFIG_PATH."""
    config = tmp_path / "hub.yaml"
    config.write_text(
        "deployment:\n  mode: offline\n"
        "storage:\n  root: /configured/data\n"
        "database:\n  url: sqlite:////configured/data/hub.db\n"
        "uploads:\n  max_size_bytes: 2048\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HUB_CONFIG_PATH", str(config))

    try:
        app = importlib.reload(main).app
        assert app.state.settings.storage.root == Path("/configured/data")
        assert app.state.settings.database.url == "sqlite:////configured/data/hub.db"
        assert app.state.settings.uploads.max_size_bytes == 2048
    finally:
        monkeypatch.setenv(
            "HUB_CONFIG_PATH", str(Path(__file__).parent / "fixtures" / "hub.yaml")
        )
        importlib.reload(main)


def test_asgi_app_rejects_invalid_hub_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid YAML configuration aborts module-level ASGI application startup."""
    config = tmp_path / "invalid-hub.yaml"
    config.write_text("- not-a-mapping\n", encoding="utf-8")
    monkeypatch.setenv("HUB_CONFIG_PATH", str(config))

    try:
        with pytest.raises(ValueError, match="根节点必须是对象"):
            importlib.reload(main)
    finally:
        monkeypatch.setenv(
            "HUB_CONFIG_PATH", str(Path(__file__).parent / "fixtures" / "hub.yaml")
        )
        importlib.reload(main)
