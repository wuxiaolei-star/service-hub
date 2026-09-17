from __future__ import annotations

from pathlib import Path

import pytest

from deploy.service_hub.entrypoint import (
    EntrypointError,
    read_runtime_token,
    validate_socket_group_isolation,
)


def test_entrypoint_rejects_a_socket_gid_shared_with_hub_data() -> None:
    """A Socket group collision would grant Docker access to the API and conda runner."""
    with pytest.raises(EntrypointError, match="hub-data"):
        validate_socket_group_isolation(socket_gid=65532, hub_data_gid=65532)


def test_entrypoint_accepts_a_distinct_socket_gid() -> None:
    """The Docker runner may join a Socket group distinct from the shared data group."""
    validate_socket_group_isolation(socket_gid=999, hub_data_gid=65532)


def test_runtime_token_with_shell_syntax_is_never_evaluated(tmp_path: Path) -> None:
    """Runtime token contents remain literal data even when they resemble command syntax."""
    marker = tmp_path / "executed"
    token = f"$(touch {marker.as_posix()})"
    runtime_environment = tmp_path / "runtime.env"
    runtime_environment.write_text(f"HUB_RUNNER_TOKEN={token}\n", encoding="utf-8")

    assert read_runtime_token(runtime_environment) == token
    assert not marker.exists()


def test_entrypoint_shell_uses_the_safe_runtime_token_and_gid_guards() -> None:
    """The POSIX entrypoint delegates parsing and GID isolation to tested helpers."""
    entrypoint = Path("docker-entrypoint.sh").read_text(encoding="utf-8")

    assert "read-runtime-token" in entrypoint
    assert "check-socket-gid" in entrypoint
    assert ". /run/service-hub/runtime.env" not in entrypoint


def test_entrypoint_grants_the_data_root_to_the_shared_group() -> None:
    """hub-api seeds the admin credential at the data root, which Docker owns as root.

    Without group write access there, a fresh deployment cannot write the credential
    and therefore never seeds a usable admin account.
    """
    entrypoint = Path("docker-entrypoint.sh").read_text(encoding="utf-8")

    assert 'chgrp hub-data "$data_root"' in entrypoint
    assert 'chmod 2775 "$data_root"' in entrypoint
