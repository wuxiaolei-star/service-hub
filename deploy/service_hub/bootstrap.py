from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


def validate_host_data_dir(value: str) -> Path:
    """Return a safe POSIX deployment directory supplied by the host."""
    data_dir = Path(value)
    normalized = data_dir.as_posix()
    if not normalized.startswith("/"):
        raise ValueError("HUB_HOST_DATA_DIR must be an absolute POSIX path")
    if normalized in {"/", "/srv", "/tmp", "/var", "/data"}:
        raise ValueError("HUB_HOST_DATA_DIR is too broad")
    if len(data_dir.parts) < 3:
        raise ValueError("HUB_HOST_DATA_DIR must have at least two path components")
    return data_dir


def load_or_create_runner_token(data_root: Path, explicit: str | None) -> str:
    """Use an explicit token or create and persist one with restrictive permissions."""
    if explicit is not None:
        return explicit

    secrets_dir = data_root / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secrets_dir.chmod(0o700)
    token_file = secrets_dir / "runner-token"
    try:
        descriptor = os.open(
            token_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        token_file.chmod(0o600)
        return token_file.read_text(encoding="utf-8")

    token = secrets.token_hex(32)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(token)
    token_file.chmod(0o600)
    return token


def docker_socket_gid(socket_path: Path) -> int:
    """Return the group ID of a Unix socket, rejecting all other file types."""
    socket_stat = socket_path.stat()
    if not stat.S_ISSOCK(socket_stat.st_mode):
        raise ValueError(f"Docker socket path is not a socket: {socket_path}")
    return socket_stat.st_gid


def write_runtime_environment(destination: Path, token: str) -> None:
    """Write the runner token to the runtime environment file with mode 0600."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(f"HUB_RUNNER_TOKEN={token}\n")
    destination.chmod(0o600)


def main() -> int:
    """Initialize persistent state and runtime environment for the container."""
    validate_host_data_dir(os.environ["HUB_HOST_DATA_DIR"])
    data_root = Path(os.environ.get("HUB_DATA_ROOT", "/data"))
    secrets_dir = data_root / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secrets_dir.chmod(0o700)
    token = load_or_create_runner_token(data_root, os.environ.get("HUB_RUNNER_TOKEN"))
    runtime_environment = Path(os.environ.get("HUB_RUNTIME_ENV", "/run/service-hub/runtime.env"))
    write_runtime_environment(runtime_environment, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
