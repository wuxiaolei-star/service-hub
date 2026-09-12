from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy.service_hub.bootstrap import (
    docker_socket_gid,
    load_or_create_runner_token,
    main,
    validate_host_data_dir,
    write_runtime_environment,
)


@pytest.mark.parametrize("value", ["", ".", "data", "/", "/srv", "/tmp"])
def test_validate_host_data_dir_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        validate_host_data_dir(value)


def test_validate_host_data_dir_accepts_deployment_path() -> None:
    assert validate_host_data_dir("/srv/service-hub-data").as_posix() == "/srv/service-hub-data"


@pytest.mark.parametrize("value", ["/srv/service-hub-data/../../", "/data/child/../.."])
def test_validate_host_data_dir_rejects_paths_that_normalize_to_unsafe_roots(value: str) -> None:
    with pytest.raises(ValueError):
        validate_host_data_dir(value)


def test_validate_host_data_dir_normalizes_safe_dot_segments() -> None:
    assert (
        validate_host_data_dir("/srv/./service-hub-data/releases/../archive").as_posix()
        == "/srv/service-hub-data/archive"
    )


def test_generated_token_is_private_and_stable(tmp_path: Path) -> None:
    first = load_or_create_runner_token(tmp_path, None)
    second = load_or_create_runner_token(tmp_path, None)

    token_file = tmp_path / "secrets" / "runner-token"
    assert first == second
    assert len(first) >= 64
    if os.name != "nt":
        assert token_file.stat().st_mode & 0o777 == 0o600


def test_explicit_token_does_not_replace_persisted_token(tmp_path: Path) -> None:
    assert load_or_create_runner_token(tmp_path, "x" * 64) == "x" * 64
    assert not (tmp_path / "secrets" / "runner-token").exists()


def test_docker_socket_gid_returns_gid_for_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        Path,
        "stat",
        lambda _path: SimpleNamespace(st_mode=stat.S_IFSOCK, st_gid=987),
    )

    assert docker_socket_gid(Path("/var/run/docker.sock")) == 987


def test_docker_socket_gid_rejects_a_non_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        Path,
        "stat",
        lambda _path: SimpleNamespace(st_mode=stat.S_IFREG, st_gid=987),
    )

    with pytest.raises(ValueError):
        docker_socket_gid(Path("/var/run/docker.sock"))


def test_write_runtime_environment_writes_private_token_file(tmp_path: Path) -> None:
    destination = tmp_path / "runtime" / "runtime.env"

    write_runtime_environment(destination, "runner-token")

    assert destination.read_text(encoding="utf-8") == "HUB_RUNNER_TOKEN=runner-token\n"
    if os.name != "nt":
        assert destination.stat().st_mode & 0o777 == 0o600


def test_main_bootstraps_data_and_runtime_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_root = tmp_path / "data"
    runtime_environment = tmp_path / "run" / "runtime.env"
    monkeypatch.setenv("HUB_HOST_DATA_DIR", "/srv/service-hub-data")
    monkeypatch.setenv("HUB_RUNNER_TOKEN", "x" * 64)
    monkeypatch.setenv("HUB_DATA_ROOT", str(data_root))
    monkeypatch.setenv("HUB_RUNTIME_ENV", str(runtime_environment))

    assert main() == 0
    assert (data_root / "secrets").is_dir()
    assert runtime_environment.read_text(encoding="utf-8") == f"HUB_RUNNER_TOKEN={'x' * 64}\n"
    if os.name != "nt":
        assert runtime_environment.stat().st_mode & 0o777 == 0o600
