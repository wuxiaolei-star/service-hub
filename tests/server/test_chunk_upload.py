"""Chunked multi-request upload API contract tests for large Hub files."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import AuditLogRecord, FileRecord, UserRecord
from hub_server.services.auth import AuthService, hash_password
from hub_server.services.retention import RetentionSweeper
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    QuotasSettings,
    RetentionSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Serve the chunked upload API with required auth and enabled quotas."""
    settings = HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),
        quotas=QuotasSettings(),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _headers(client: TestClient, username: str, role: str) -> dict[str, str]:
    """Get or create one user and return bearer credentials for a fresh session."""
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter_by(username=username).one_or_none()
        if user is None:
            user = UserRecord(
                username=username,
                password_hash=hash_password("unused-password"),
                role=role,
            )
            session.add(user)
            session.commit()
        _record, token = AuthService(session).create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def _operator_headers(client: TestClient) -> dict[str, str]:
    return _headers(client, "chunk-operator", "operator")


def _staging_root(client: TestClient) -> Path:
    return Path(client.app.state.settings.storage.root) / "uploads"


def _staging_directories(client: TestClient) -> list[Path]:
    root = _staging_root(client)
    if not root.is_dir():
        return []
    return [entry for entry in root.iterdir() if entry.is_dir()]


def test_chunk_init_creates_staging_directory_and_audits(client: TestClient) -> None:
    """An initialized chunk upload must reserve a staging directory and audit the event."""
    response = client.post(
        "/api/v1/files/chunk/init",
        headers=_operator_headers(client),
        json={"filename": "big.nc", "total_size": 10},
    )

    assert response.status_code == 201
    upload_id = response.json()["upload_id"]
    assert len(upload_id) == 32
    int(upload_id, 16)  # must be a hex staging identifier
    assert (_staging_root(client) / upload_id).is_dir()

    factory = client.app.state.session_factory
    with factory() as session:
        entry = session.query(AuditLogRecord).filter_by(action="file.chunk_init").one()
        assert entry.resource_id == upload_id
        assert entry.actor_name == "chunk-operator"


def test_chunk_put_writes_index_named_chunk_file(client: TestClient) -> None:
    """Each raw-body chunk must land under its zero-padded index name."""
    created = client.post(
        "/api/v1/files/chunk/init",
        headers=_operator_headers(client),
        json={"filename": "big.nc", "total_size": 5},
    )
    upload_id = created.json()["upload_id"]

    stored = client.put(
        f"/api/v1/files/chunk/{upload_id}/0",
        headers=_operator_headers(client),
        content=b"hello",
    )

    assert stored.status_code == 200
    assert stored.json() == {"chunk_index": 0, "size": 5}
    chunk = _staging_root(client) / upload_id / "chunk_0000"
    assert chunk.is_file()
    assert chunk.read_bytes() == b"hello"


def test_chunk_complete_merges_out_of_order_chunks_into_one_record(
    client: TestClient,
) -> None:
    """Completion must concatenate chunks by index, regardless of upload order."""
    headers = _operator_headers(client)
    created = client.post(
        "/api/v1/files/chunk/init",
        headers=headers,
        json={"filename": "model.nc", "total_size": 10},
    )
    upload_id = created.json()["upload_id"]
    assert client.put(
        f"/api/v1/files/chunk/{upload_id}/1", headers=headers, content=b"world"
    ).status_code == 200
    assert client.put(
        f"/api/v1/files/chunk/{upload_id}/0", headers=headers, content=b"hello"
    ).status_code == 200

    completed = client.post(
        f"/api/v1/files/chunk/{upload_id}/complete",
        headers=headers,
        json={"filename": "model.nc", "total_chunks": 2},
    )

    assert completed.status_code == 201
    body = completed.json()
    payload = b"hello" + b"world"
    assert body["name"] == "model.nc"
    assert body["extension"] == ".nc"
    assert body["size"] == len(payload)
    assert body["sha256"] == hashlib.sha256(payload).hexdigest()
    assert body["status"] == "AVAILABLE"
    file_key = body["file_id"]
    assert client.get(f"/api/v1/files/{file_key}/download", headers=headers).content == payload

    staging = _staging_root(client) / upload_id
    assert list(staging.glob("chunk_*")) == []
    assert (staging / "payload").is_file()

    factory = client.app.state.session_factory
    with factory() as session:
        record = session.query(FileRecord).filter_by(file_key=file_key).one()
        operator = session.query(UserRecord).filter_by(username="chunk-operator").one()
        assert record.owner_user_id == operator.id
        entry = session.query(AuditLogRecord).filter_by(action="file.chunk_complete").one()
        assert entry.resource_id == file_key


def test_chunk_put_without_init_returns_404(client: TestClient) -> None:
    """Chunks for an unknown staging identifier must use the stable 404 error."""
    response = client.put(
        f"/api/v1/files/chunk/{uuid.uuid4().hex}/0",
        headers=_operator_headers(client),
        content=b"orphan",
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHUNK_UPLOAD_NOT_FOUND"


def test_chunk_complete_with_missing_chunk_returns_409(client: TestClient) -> None:
    """An incomplete set of chunks must be rejected and remain retryable."""
    headers = _operator_headers(client)
    created = client.post(
        "/api/v1/files/chunk/init",
        headers=headers,
        json={"filename": "model.nc", "total_size": 10},
    )
    upload_id = created.json()["upload_id"]
    assert client.put(
        f"/api/v1/files/chunk/{upload_id}/0", headers=headers, content=b"hello"
    ).status_code == 200

    completed = client.post(
        f"/api/v1/files/chunk/{upload_id}/complete",
        headers=headers,
        json={"filename": "model.nc", "total_chunks": 2},
    )

    assert completed.status_code == 409
    assert completed.json()["error"]["code"] == "CHUNK_UPLOAD_INCOMPLETE"
    factory = client.app.state.session_factory
    with factory() as session:
        assert session.query(FileRecord).count() == 0
    # The staging directory must survive so the client can send the missing part.
    assert (_staging_root(client) / upload_id / "chunk_0000").is_file()


def test_chunk_complete_unknown_upload_returns_404(client: TestClient) -> None:
    """Completing an unknown staging identifier must use the same 404 error."""
    response = client.post(
        f"/api/v1/files/chunk/{uuid.uuid4().hex}/complete",
        headers=_operator_headers(client),
        json={"filename": "model.nc", "total_chunks": 1},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHUNK_UPLOAD_NOT_FOUND"


def test_chunk_init_over_limit_returns_413_without_staging(client: TestClient) -> None:
    """A declared total above the upload cap must be refused before any reservation."""
    response = client.post(
        "/api/v1/files/chunk/init",
        headers=_operator_headers(client),
        json={"filename": "big.nc", "total_size": 1025},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
    assert _staging_directories(client) == []
    factory = client.app.state.session_factory
    with factory() as session:
        assert session.query(AuditLogRecord).filter_by(action="file.chunk_init").count() == 0


def test_viewer_cannot_start_chunk_upload(client: TestClient) -> None:
    """The viewer role stays below the operator floor of the chunk endpoints."""
    response = client.post(
        "/api/v1/files/chunk/init",
        headers=_headers(client, "chunk-viewer", "viewer"),
        json={"filename": "big.nc", "total_size": 10},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_anonymous_cannot_start_chunk_upload(client: TestClient) -> None:
    """Chunked uploads require credentials like every other file write."""
    response = client.post(
        "/api/v1/files/chunk/init",
        json={"filename": "big.nc", "total_size": 10},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_quota_exceeded_at_complete_returns_409_and_cleans_staging(
    client: TestClient,
) -> None:
    """The merged size must pass quota enforcement and failures leave nothing behind."""
    headers = _operator_headers(client)
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter_by(username="chunk-operator").one()
        user.quota_total_bytes = 4
        session.commit()
    created = client.post(
        "/api/v1/files/chunk/init",
        headers=headers,
        json={"filename": "model.nc", "total_size": 10},
    )
    upload_id = created.json()["upload_id"]
    assert client.put(
        f"/api/v1/files/chunk/{upload_id}/0", headers=headers, content=b"0123456789"
    ).status_code == 200

    completed = client.post(
        f"/api/v1/files/chunk/{upload_id}/complete",
        headers=headers,
        json={"filename": "model.nc", "total_chunks": 1},
    )

    assert completed.status_code == 409
    assert completed.json()["error"]["code"] == "QUOTA_EXCEEDED"
    with factory() as session:
        assert session.query(FileRecord).count() == 0
    assert (_staging_root(client) / upload_id).exists() is False


def test_retention_sweeper_removes_stale_chunk_staging_directories(
    client: TestClient,
) -> None:
    """Only staging directories idle beyond 24h are garbage-collected by the sweeper."""
    headers = _operator_headers(client)
    created = client.post(
        "/api/v1/files/chunk/init",
        headers=headers,
        json={"filename": "model.nc", "total_size": 5},
    )
    upload_id = created.json()["upload_id"]
    assert client.put(
        f"/api/v1/files/chunk/{upload_id}/0", headers=headers, content=b"hello"
    ).status_code == 200

    root = _staging_root(client)
    stale = datetime.now(UTC) - timedelta(hours=25)
    stale_empty = root / uuid.uuid4().hex
    stale_empty.mkdir()
    stale_partial = root / uuid.uuid4().hex
    stale_partial.mkdir()
    (stale_partial / "chunk_0000").write_bytes(b"stale")
    completed_old = root / uuid.uuid4().hex
    completed_old.mkdir()
    (completed_old / "payload").write_bytes(b"installed")
    installed_old = root / f"file_{uuid.uuid4().hex}"
    installed_old.mkdir()
    (installed_old / "payload").write_bytes(b"installed")
    for directory in (stale_empty, stale_partial, completed_old, installed_old):
        os.utime(directory, (stale.timestamp(), stale.timestamp()))

    factory = client.app.state.session_factory
    with factory() as session:
        deleted = RetentionSweeper(
            session, root.parent, RetentionSettings(input_ttl_hours=720)
        ).sweep()

    assert deleted == 2
    assert not stale_empty.exists()
    assert not stale_partial.exists()
    assert (root / upload_id).is_dir()  # fresh staging survives
    assert completed_old.is_dir()  # payload-bearing directories survive
    assert installed_old.is_dir()  # installed file_ uploads survive
