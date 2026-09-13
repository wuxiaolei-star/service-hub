"""Auth command contract tests for hubctl (login/logout/whoami, token loading)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.tools.test_hubctl import load_hubctl


class FakeTransport:
    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.token: str | None = None
        self.responses = responses or {}
        self.json_calls: list[tuple[str, str, object | None]] = []

    def request_json(self, method: str, path: str, body: object | None) -> object:
        self.json_calls.append((method, path, body))
        if path in self.responses:
            value = self.responses[path]
            if isinstance(value, Exception):
                raise value
            return value
        return {"ok": True}


@pytest.fixture
def hubctl() -> ModuleType:
    return load_hubctl()


def _write_credentials(home: Path, token: str) -> None:
    hubctl_dir = home / ".hubctl"
    hubctl_dir.mkdir(parents=True)
    (hubctl_dir / "credentials").write_text(json.dumps({"token": token}), encoding="utf-8")


def test_login_prompts_password_and_persists_credentials(
    hubctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("hubctl"))
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": "s3cret-password")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    transport = FakeTransport(
        {"/api/v1/auth/login": {"token": "session-token-1", "role": "admin"}}
    )

    exit_code = hubctl.main(["login"], transport=transport)

    assert exit_code == 0
    method, path, body = transport.json_calls[-1]
    assert (method, path) == ("POST", "/api/v1/auth/login")
    assert body == {"username": "hubctl", "password": "s3cret-password"}
    saved = json.loads((tmp_path / ".hubctl" / "credentials").read_text(encoding="utf-8"))
    assert saved["token"] == "session-token-1"
    mode = (tmp_path / ".hubctl" / "credentials").stat().st_mode & 0o777
    if sys.platform != "win32":
        assert mode == 0o600
    captured = capsys.readouterr()
    assert "session-token-1" not in captured.err


def test_whoami_uses_hub_token_env_over_credentials(
    hubctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _write_credentials(tmp_path, "file-token")
    monkeypatch.setenv("HUB_TOKEN", "env-token")
    transport = FakeTransport({"/api/v1/auth/me": {"username": "admin", "role": "admin"}})

    exit_code = hubctl.main(["whoami"], transport=transport)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert transport.json_calls[0][1] == "/api/v1/auth/me"
    assert json.loads(output)["role"] == "admin"
    assert transport.token == "env-token"


def test_credentials_file_token_is_loaded_when_env_absent(
    hubctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("HUB_TOKEN", raising=False)
    _write_credentials(tmp_path, "file-token")
    transport = FakeTransport()

    hubctl.main(["whoami"], transport=transport)

    assert transport.token == "file-token"


def test_logout_revokes_and_removes_credentials(
    hubctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _write_credentials(tmp_path, "file-token")
    monkeypatch.delenv("HUB_TOKEN", raising=False)
    transport = FakeTransport()

    exit_code = hubctl.main(["logout"], transport=transport)

    assert exit_code == 0
    assert transport.json_calls[0][:2] == ("POST", "/api/v1/auth/logout")
    assert not (tmp_path / ".hubctl" / "credentials").exists()


def test_auth_required_error_prints_relogin_hint(
    hubctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("HUB_TOKEN", raising=False)
    transport = FakeTransport(
        {"/api/v1/auth/me": hubctl.HubApiError("AUTH_REQUIRED", "请先登录")}
    )

    exit_code = hubctl.main(["whoami"], transport=transport)
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "登录" in captured.err
