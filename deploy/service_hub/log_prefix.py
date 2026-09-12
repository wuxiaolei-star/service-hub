"""Prefix a supervised program's combined output without changing its process group."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence


def run_prefixed(label: str, command: Sequence[str]) -> int:
    """Run a command and write its combined output with a stable service prefix.

    The child deliberately stays in the wrapper's process group. Supervisord's
    ``stopasgroup`` and ``killasgroup`` therefore signal both processes directly.
    """
    if not command:
        raise ValueError("a wrapped command is required")

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if process.stdout is None:
        raise RuntimeError("wrapped process did not provide stdout")

    prefix = f"[{label}] ".encode()
    with process.stdout:
        for line in iter(process.stdout.readline, b""):
            sys.stdout.buffer.write(prefix + line)
            sys.stdout.buffer.flush()
    return process.wait()


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``LABEL -- COMMAND`` and return the managed process exit status."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) < 3 or arguments[1] != "--":
        print("usage: log_prefix LABEL -- COMMAND", file=sys.stderr)
        return 2
    return run_prefixed(arguments[0], arguments[2:])


if __name__ == "__main__":
    raise SystemExit(main())
