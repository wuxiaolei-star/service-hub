import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault(
    "HUB_CONFIG_PATH",
    str(Path(__file__).parent / "fixtures" / "hub.yaml"),
)

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.settings import (
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    StorageSettings,
    UploadSettings,
)
from sqlalchemy.orm import Session


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    """Provide a session backed by a database upgraded through every migration."""
    settings = HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
    )
    app = create_app(settings)
    with TestClient(app), app.state.session_factory() as database_session:
        yield database_session
