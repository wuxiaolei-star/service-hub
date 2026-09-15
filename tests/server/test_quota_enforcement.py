"""Quota enforcement contract tests for uploads and job creation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import FileRecord, UserRecord
from hub_server.services.auth import hash_password
from hub_server.services.quotas import QuotaService, UploadCap
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    QuotasSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)

from tests.server.test_jobs_api import _seed_build


def _settings(
    tmp_path: Path,
    *,
    quotas: QuotasSettings | None = None,
) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),  # type: ignore[arg-type]
        quotas=quotas or QuotasSettings(),
    )


def _add_user(client: TestClient, username: str, role: str, **quota: int) -> None:
    factory = client.app.state.session_factory
    with factory() as session:
        session.add(
            UserRecord(
                username=username,
                password_hash=hash_password("password-secret"),
                role=role,
                **quota,
            )
        )
        session.commit()


def _token_for(client: TestClient, username: str) -> str:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == username).one()
        from hub_server.services.auth import AuthService

        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return token


def _client(tmp_path: Path, quotas: QuotasSettings | None = None) -> TestClient:
    return TestClient(create_app(_settings(tmp_path, quotas=quotas)))


def _upload(client: TestClient, token: str, payload: bytes, name: str = "a.nc") -> object:
    return client.post(
        "/api/v1/files",
        files={"file": (name, payload, "application/x-netcdf")},
        headers={"Authorization": f"Bearer {token}"},
    )


def _file_record_count(client: TestClient) -> int:
    with client.app.state.session_factory() as session:
        return session.query(FileRecord).count()


def _stored_payloads(client: TestClient) -> list[Path]:
    """Every upload directory, including one an interrupted stream left behind."""
    uploads = client.app.state.settings.storage.root / "uploads"
    return sorted(uploads.glob("file_*")) if uploads.exists() else []


def _chunked_upload(
    client: TestClient, token: str, body: bytes, boundary: str
) -> tuple[int, dict[str, object]]:
    """POST a multipart body in raw chunks, declaring no Content-Length.

    A chunked body is exactly what a declared-size pre-check cannot see, so the
    request is assembled at the ASGI layer with only the headers such a client
    would send.
    """
    pending = [body[index : index + 512] for index in range(0, len(body), 512)]
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        if not pending:
            return {"type": "http.request", "body": b"", "more_body": False}
        chunk = pending.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    async def call() -> None:
        await client.app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/v1/files",
                "raw_path": b"/api/v1/files",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"host", b"testserver"),
                    (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
                    (b"authorization", f"Bearer {token}".encode()),
                ],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )

    client.portal.call(call)
    start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    payload = b"".join(
        message["body"]
        for message in messages
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), json.loads(payload)


def test_file_count_quota_uses_user_override_then_409(tmp_path: Path) -> None:
    quotas = QuotasSettings(max_file_count=100)
    with _client(tmp_path, quotas) as client:
        _add_user(client, "op", "operator", quota_file_count=1)
        token = _token_for(client, "op")

        assert _upload(client, token, b"data-1").status_code == 201  # type: ignore[union-attr]
        second = _upload(client, token, b"data-2")
        assert second.status_code == 409  # type: ignore[union-attr]
        body = second.json()["error"]  # type: ignore[union-attr]
        assert body["code"] == "QUOTA_EXCEEDED"
        assert body["details"]["quota"] == "file_count"


def test_total_bytes_quota_uses_user_override(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _add_user(client, "op", "operator", quota_total_bytes=10)
        token = _token_for(client, "op")

        response = _upload(client, token, b"x" * 20)
        assert response.status_code == 409  # type: ignore[union-attr]
        body = response.json()["error"]  # type: ignore[union-attr]
        assert body["details"]["quota"] == "total_bytes"
        assert body["details"]["limit"] == 10


def test_admin_role_is_exempt_from_user_quotas(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _add_user(client, "boss", "admin", quota_file_count=1)
        token = _token_for(client, "boss")

        assert _upload(client, token, b"one").status_code == 201  # type: ignore[union-attr]
        assert _upload(client, token, b"two", name="b.nc").status_code == 201  # type: ignore[union-attr]


def test_disabled_quotas_allow_everything(tmp_path: Path) -> None:
    quotas = QuotasSettings(enabled=False, max_file_count=1)
    with _client(tmp_path, quotas) as client:
        _add_user(client, "op", "operator", quota_file_count=1)
        token = _token_for(client, "op")

        assert _upload(client, token, b"one").status_code == 201  # type: ignore[union-attr]
        assert _upload(client, token, b"two", name="b.nc").status_code == 201  # type: ignore[union-attr]


def test_api_key_actor_is_limited_only_by_global_bytes(tmp_path: Path) -> None:
    quotas = QuotasSettings(max_total_bytes=10)
    with _client(tmp_path, quotas) as client:
        _add_user(client, "op", "operator")
        operator_token = _token_for(client, "op")

        from hub_server.services.auth import AuthService

        factory = client.app.state.session_factory
        with factory() as session:
            service = AuthService(session)
            _record, key = service.create_api_key(name="ci", role="operator")
            session.commit()

        response = _upload(client, key, b"x" * 20)
        assert response.status_code == 409  # type: ignore[union-attr]
        assert response.json()["error"]["details"]["quota"] == "global_bytes"  # type: ignore[union-attr]

        headers = {"Authorization": f"Bearer {operator_token}"}
        list_response = client.get("/api/v1/files", headers=headers)
        assert list_response.status_code == 200
        assert list_response.json()["items"] == []


def test_job_concurrency_quota_per_user(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_build(client, runtime_type="docker")
        _add_user(client, "op", "operator", quota_concurrent_jobs=1)
        token = _token_for(client, "op")
        headers = {"Authorization": f"Bearer {token}"}

        uploaded = _upload(client, token, b"nc")
        assert uploaded.status_code == 201  # type: ignore[union-attr]
        file_id = uploaded.json()["file_id"]  # type: ignore[union-attr]

        first = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={
                "plugin_id": "nc_to_shp",
                "version": "1.0.0",
                "runtime_type": "docker",
                "inputs": {"source_nc": file_id},
                "params": {},
            },
        )
        assert first.status_code == 201

        second = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={
                "plugin_id": "nc_to_shp",
                "version": "1.0.0",
                "runtime_type": "docker",
                "inputs": {"source_nc": file_id},
                "params": {},
            },
        )
        assert second.status_code == 409  # type: ignore[union-attr]
        body = second.json()["error"]  # type: ignore[union-attr]
        assert body["code"] == "QUOTA_EXCEEDED"
        assert body["details"]["quota"] == "concurrent_jobs"


def test_chunked_upload_cannot_outgrow_the_remaining_quota(tmp_path: Path) -> None:
    """A body with no declared length must still stop inside the account's headroom."""
    boundary = "quota-boundary"
    with _client(tmp_path) as client:
        _add_user(client, "op", "operator", quota_total_bytes=64)
        token = _token_for(client, "op")
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="big.nc"\r\n'
            "Content-Type: application/x-netcdf\r\n\r\n"
        ).encode() + b"x" * 4096 + f"\r\n--{boundary}--\r\n".encode()

        status, payload = _chunked_upload(client, token, body, boundary)

        assert status == 409
        assert payload["error"]["code"] == "QUOTA_EXCEEDED"  # type: ignore[index]
        assert payload["error"]["details"]["quota"] == "total_bytes"  # type: ignore[index]
        assert _file_record_count(client) == 0
        assert _stored_payloads(client) == []


def test_a_rejected_upload_keeps_neither_record_nor_payload(tmp_path: Path) -> None:
    """Committing metadata before the quota check used to leave orphans behind."""
    with _client(tmp_path) as client:
        _add_user(client, "op", "operator", quota_file_count=1)
        token = _token_for(client, "op")

        assert _upload(client, token, b"data-1").status_code == 201  # type: ignore[union-attr]
        rejected = _upload(client, token, b"data-2")

        assert rejected.status_code == 409  # type: ignore[union-attr]
        assert _file_record_count(client) == 1
        assert len(_stored_payloads(client)) == 1


def test_a_stale_precheck_cannot_commit_an_over_quota_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check that runs against the flushed row is the one that decides.

    Forcing the cap to claim unlimited headroom leaves the declared-size guard
    and the stream guard agreeing with the client, so only the verification
    inside the metadata transaction can reject the upload - and it must leave
    neither a record nor a payload behind.
    """
    with _client(tmp_path) as client:
        _add_user(client, "op", "operator", quota_total_bytes=10)
        token = _token_for(client, "op")
        monkeypatch.setattr(
            QuotaService,
            "upload_cap",
            lambda self, actor, *, upload_max_bytes: UploadCap(
                upload_max_bytes, None, 0, None
            ),
        )

        response = _upload(client, token, b"x" * 20)

        assert response.status_code == 409  # type: ignore[union-attr]
        assert response.json()["error"]["details"]["quota"] == "total_bytes"  # type: ignore[union-attr]
        assert _file_record_count(client) == 0
        assert _stored_payloads(client) == []
