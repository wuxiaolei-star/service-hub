"""Contract tests for login throttling and forwarded-address resolution."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from hub_server.dependencies_auth import actor_ip
from hub_server.main import create_app
from hub_server.models import AuditLogRecord, LoginAttemptRecord
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)
from starlette.requests import Request

# The socket peer the console's edge proxy presents to hub-api: a private
# address inside the trusted proxy segments (audit M-6).
_PROXY_CLIENT = ("172.18.0.9", 50000)


def _settings(tmp_path: Path, **auth: object) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required", **auth),  # type: ignore[arg-type]
    )


def _credentials(client: TestClient) -> dict[str, str]:
    path = client.app.state.settings.storage.root / "bootstrap-admin.json"
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _login(
    client: TestClient, username: str, password: str, *, forwarded: str | None = None
) -> httpx.Response:
    headers = {"X-Forwarded-For": forwarded} if forwarded is not None else {}
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=headers,
    )


def _expire_locks(client: TestClient) -> None:
    """Age every recorded lock so the next failure starts a new lock window."""
    with client.app.state.session_factory() as session:
        for record in session.query(LoginAttemptRecord).all():
            record.locked_until = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()


def _throttle_settings(tmp_path: Path, **auth: object) -> HubSettings:
    defaults: dict[str, object] = {
        "login_failure_threshold": 2,
        "login_lockout_base_seconds": 30,
        "login_lockout_max_seconds": 300,
    }
    defaults.update(auth)
    return _settings(tmp_path, **defaults)


def test_failures_past_the_threshold_produce_a_retry_after(tmp_path: Path) -> None:
    with TestClient(create_app(_throttle_settings(tmp_path))) as client:
        assert _login(client, "admin", "wrong").status_code == 401
        assert _login(client, "admin", "wrong").status_code == 401

        locked = _login(client, "admin", "wrong")

        assert locked.status_code == 429
        body = locked.json()["error"]
        assert body["code"] == "TOO_MANY_ATTEMPTS"
        assert body["details"] == {"retry_after_seconds": 30}
        assert locked.headers["retry-after"] == "30"


def test_a_locked_identity_is_refused_even_with_the_right_password(tmp_path: Path) -> None:
    """The refusal must not depend on the credential, or it becomes an oracle."""
    with TestClient(create_app(_throttle_settings(tmp_path))) as client:
        credentials = _credentials(client)
        assert _login(client, "admin", "wrong").status_code == 401
        assert _login(client, "admin", "wrong").status_code == 401
        assert _login(client, "admin", "wrong").status_code == 429

        correct = _login(client, credentials["username"], credentials["password"])

        assert correct.status_code == 429


def test_each_new_failure_after_a_lock_doubles_the_wait(tmp_path: Path) -> None:
    with TestClient(create_app(_throttle_settings(tmp_path))) as client:
        for _ in range(3):
            _login(client, "admin", "wrong")

        # Each attempt that gets past an expired lock starts a longer one, and
        # the configured ceiling holds the growth in place.
        for expected in (60, 120, 240, 300):
            _expire_locks(client)
            again = _login(client, "admin", "wrong")
            assert again.status_code == 429
            assert again.json()["error"]["details"] == {"retry_after_seconds": expected}


def test_successful_login_clears_the_failure_counter(tmp_path: Path) -> None:
    with TestClient(create_app(_throttle_settings(tmp_path))) as client:
        credentials = _credentials(client)
        assert _login(client, "admin", "wrong").status_code == 401
        assert _login(client, "admin", "wrong").status_code == 401

        assert (
            _login(client, credentials["username"], credentials["password"]).status_code == 200
        )
        with client.app.state.session_factory() as session:
            assert session.query(LoginAttemptRecord).count() == 0

        # The counter restarted, so the same two failures are refused, not locked.
        assert _login(client, "admin", "wrong").status_code == 401
        assert _login(client, "admin", "wrong").status_code == 401


def test_one_attacked_account_does_not_lock_out_another(tmp_path: Path) -> None:
    with TestClient(create_app(_throttle_settings(tmp_path, login_failure_threshold=1))) as client:
        credentials = _credentials(client)
        assert _login(client, "ghost", "wrong").status_code == 401
        assert _login(client, "ghost", "wrong").status_code == 429

        assert (
            _login(client, credentials["username"], credentials["password"]).status_code == 200
        )


def test_the_refusal_looks_the_same_for_a_missing_account(tmp_path: Path) -> None:
    with TestClient(create_app(_throttle_settings(tmp_path, login_failure_threshold=1))) as client:
        for username in ("ghost", "admin"):
            assert _login(client, username, "wrong").status_code == 401
            assert _login(client, username, "wrong").status_code == 429

        missing = _login(client, "ghost", "wrong").json()["error"]
        existing = _login(client, "admin", "wrong").json()["error"]

        assert missing["code"] == existing["code"] == "TOO_MANY_ATTEMPTS"
        assert missing["message"] == existing["message"]


def test_each_forwarded_address_gets_its_own_bucket(tmp_path: Path) -> None:
    """Behind the trusted edge proxy the forwarded client address is honoured."""
    with TestClient(
        create_app(_throttle_settings(tmp_path, login_failure_threshold=1)), client=_PROXY_CLIENT
    ) as client:
        assert (
            _login(client, "admin", "wrong", forwarded="203.0.113.7").status_code == 401
        )
        assert (
            _login(client, "admin", "wrong", forwarded="203.0.113.7").status_code == 429
        )
        # A different client must not inherit the first one's lockout.
        assert (
            _login(client, "admin", "wrong", forwarded="198.51.100.9").status_code == 401
        )


def test_password_spraying_across_usernames_hits_the_ip_aggregate(tmp_path: Path) -> None:
    """Failures spread over many usernames still stop at the per-address cap."""
    settings = _throttle_settings(
        tmp_path,
        login_failure_threshold=10,
        login_ip_failure_threshold=3,
        login_ip_window_seconds=60,
    )
    with TestClient(create_app(settings), client=_PROXY_CLIENT) as client:
        # Every per-identity bucket stays far below its own threshold...
        assert _login(client, "u1", "wrong", forwarded="203.0.113.7").status_code == 401
        assert _login(client, "u2", "wrong", forwarded="203.0.113.7").status_code == 401
        # ...but the attempt that pushes the address total over the cap is
        # refused outright, and so is every attempt after it.
        assert _login(client, "u3", "wrong", forwarded="203.0.113.7").status_code == 429
        sprayed = _login(client, "u4", "wrong", forwarded="203.0.113.7")
        assert sprayed.status_code == 429
        assert sprayed.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"

        # A different address is not dragged into the spray's refusal.
        assert _login(client, "u5", "wrong", forwarded="198.51.100.9").status_code == 401


def test_a_successful_login_does_not_clear_the_ip_aggregate(tmp_path: Path) -> None:
    """One valid credential must not unblock a spraying address."""
    settings = _throttle_settings(
        tmp_path,
        login_failure_threshold=10,
        login_ip_failure_threshold=2,
        login_ip_window_seconds=60,
    )
    with TestClient(create_app(settings), client=_PROXY_CLIENT) as client:
        assert _login(client, "ghost", "wrong", forwarded="203.0.113.7").status_code == 401
        assert _login(client, "ghost", "wrong", forwarded="203.0.113.7").status_code == 429

        # The spray continues from a different name while a legitimate user
        # behind the same address logs in successfully.
        assert _login(client, "admin", "wrong", forwarded="203.0.113.7").status_code == 429
        assert _login(client, "admin", "wrong", forwarded="203.0.113.7").status_code == 429


def test_the_ip_aggregate_lifts_once_failures_leave_the_window(tmp_path: Path) -> None:
    settings = _throttle_settings(
        tmp_path,
        login_failure_threshold=10,
        login_ip_failure_threshold=2,
        login_ip_window_seconds=60,
    )
    with TestClient(create_app(settings), client=_PROXY_CLIENT) as client:
        assert _login(client, "u1", "wrong", forwarded="203.0.113.7").status_code == 401
        assert _login(client, "u2", "wrong", forwarded="203.0.113.7").status_code == 429

        with client.app.state.session_factory() as session:
            for record in session.query(LoginAttemptRecord).all():
                record.last_failure_at = datetime.now(UTC) - timedelta(seconds=120)
            session.commit()

        assert _login(client, "u3", "wrong", forwarded="203.0.113.7").status_code == 401


def test_global_cleanup_deletes_rows_that_left_the_backoff_horizon(tmp_path: Path) -> None:
    """One-off names must not accumulate rows forever (audit L-12)."""
    settings = _throttle_settings(tmp_path, login_prune_interval_attempts=1)
    with TestClient(create_app(settings), client=_PROXY_CLIENT) as client:
        assert _login(client, "u1", "wrong").status_code == 401
        with client.app.state.session_factory() as session:
            session.add(
                LoginAttemptRecord(
                    username="one-off",
                    ip="-",
                    failure_count=7,
                    last_failure_at=datetime.now(UTC) - timedelta(hours=2),
                )
            )
            session.commit()
            assert session.query(LoginAttemptRecord).count() == 2

        # The next login attempt runs the horizon-wide cleanup (interval = 1).
        assert _login(client, "u2", "wrong").status_code == 401

        with client.app.state.session_factory() as session:
            remaining = {record.username for record in session.query(LoginAttemptRecord).all()}
        assert "one-off" not in remaining
        assert remaining == {"u1", "u2"}


def test_crossing_into_a_lock_is_recorded_for_operators(tmp_path: Path) -> None:
    with TestClient(create_app(_throttle_settings(tmp_path, login_failure_threshold=1))) as client:
        assert _login(client, "admin", "wrong").status_code == 401
        assert _login(client, "admin", "wrong").status_code == 429

        with client.app.state.session_factory() as session:
            actions = [row.action for row in session.query(AuditLogRecord).all()]

        assert "auth.login_locked" in actions


def _request(*, headers: dict[str, str], client: tuple[str, int] | None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
            "client": client,
        }
    )


def test_actor_ip_prefers_the_hop_our_proxy_appended() -> None:
    """A forged leading entry must not displace the address Nginx recorded."""
    request = _request(
        headers={"x-forwarded-for": "10.0.0.9, 203.0.113.7"}, client=("127.0.0.1", 5)
    )

    assert actor_ip(request) == "203.0.113.7"


def test_actor_ip_falls_back_to_the_socket_peer() -> None:
    request = _request(headers={}, client=("127.0.0.1", 5))

    assert actor_ip(request) == "127.0.0.1"


def test_actor_ip_ignores_an_unusable_forwarded_value() -> None:
    over_long = _request(headers={"x-forwarded-for": "a" * 200}, client=("127.0.0.1", 5))
    blank = _request(headers={"x-forwarded-for": " , "}, client=("127.0.0.1", 5))

    assert actor_ip(over_long) == "127.0.0.1"
    assert actor_ip(blank) == "127.0.0.1"


def test_actor_ip_is_absent_without_a_client() -> None:
    assert actor_ip(_request(headers={}, client=None)) is None


def test_actor_ip_ignores_a_forwarded_header_from_a_direct_connection() -> None:
    """A peer outside the trusted segments cannot forge an address (audit M-6)."""
    request = _request(
        headers={"x-forwarded-for": "198.51.100.1, 203.0.113.7"}, client=("203.0.113.5", 5)
    )

    assert actor_ip(request) == "203.0.113.5"


@pytest.mark.parametrize(
    "peer",
    ["10.1.2.3", "172.18.0.9", "192.168.1.10", "127.0.0.1", "::1", "::ffff:127.0.0.1"],
)
def test_actor_ip_honours_the_header_from_trusted_proxy_segments(peer: str) -> None:
    request = _request(headers={"x-forwarded-for": "203.0.113.7"}, client=(peer, 5))

    assert actor_ip(request) == "203.0.113.7"


def test_actor_ip_binds_the_header_to_a_public_peer_regardless_of_its_value() -> None:
    """A private address claimed in the header cannot substitute the real peer."""
    request = _request(headers={"x-forwarded-for": "10.0.0.1"}, client=("198.51.100.5", 5))

    assert actor_ip(request) == "198.51.100.5"
