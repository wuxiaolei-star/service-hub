"""Lexical safe-relative path validation shared by SDK output helpers."""

import re

MAX_PROTOCOL_PATH_LENGTH = 1024


def validate_relative_protocol_path(path: object) -> str:
    """Require canonical POSIX syntax accepted unchanged by the wire protocol."""
    if not isinstance(path, str) or not path:
        raise ValueError("file path must be a non-empty relative path")
    if len(path) > MAX_PROTOCOL_PATH_LENGTH:
        raise ValueError("file path exceeds the maximum length")
    if "\x00" in path or "\\" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise ValueError("file path must use safe relative POSIX syntax")
    if any(segment in {"", ".", ".."} for segment in path.split("/")):
        raise ValueError("file path must not contain empty, current, or parent segments")
    return path
