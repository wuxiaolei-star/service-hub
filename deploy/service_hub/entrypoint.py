"""Safe, testable primitives used by the root Service Hub entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn

_TOKEN_PREFIX = "HUB_RUNNER_TOKEN="


class EntrypointError(ValueError):
    """Raised when startup input would weaken a Service Hub security boundary."""


def validate_socket_group_isolation(socket_gid: int, hub_data_gid: int) -> None:
    """Reject a Docker Socket group that would also grant access to Hub data users."""
    if socket_gid == hub_data_gid:
        raise EntrypointError("Docker socket GID must not match the hub-data GID")


def read_runtime_token(runtime_environment: Path) -> str:
    """Read one literal runner token without evaluating shell syntax from the file."""
    try:
        lines = runtime_environment.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise EntrypointError("runtime environment is unavailable") from error

    if len(lines) != 1 or not lines[0].startswith(_TOKEN_PREFIX):
        raise EntrypointError("runtime environment must contain exactly one runner token")
    token = lines[0].removeprefix(_TOKEN_PREFIX)
    if not token:
        raise EntrypointError("runner token must not be empty")
    return token


def _fail(message: str) -> NoReturn:
    print(f"service-hub-entrypoint: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    """Provide narrow shell-facing commands without exposing runner token data."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) == 2 and arguments[0] == "read-runtime-token":
        try:
            print(read_runtime_token(Path(arguments[1])))
        except EntrypointError as error:
            _fail(str(error))
        return 0
    if len(arguments) == 3 and arguments[0] == "check-socket-gid":
        try:
            validate_socket_group_isolation(int(arguments[1]), int(arguments[2]))
        except (EntrypointError, ValueError):
            _fail("Docker socket GID conflicts with the hub-data group")
        return 0
    _fail("invalid startup helper arguments")


if __name__ == "__main__":
    raise SystemExit(main())
