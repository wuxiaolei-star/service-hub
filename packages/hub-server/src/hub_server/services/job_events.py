"""Job runner-event log locations and tolerant line decoding (audit M-2).

The runner-event stream used to live at ``<workspace>/logs/events.jsonl`` —
the same directory the plugin container receives read-write. A plugin could
therefore forge or truncate its own event log and crash the SSE stream with
garbage bytes. The stream now lives at ``<workspace>/meta/events.jsonl``;
``meta/`` is created next to the runtime directories but is deliberately
absent from the Docker volume map (see ``docker_executor.execute``), so the
plugin has no path to it. Reads stay compatible with Jobs that ran before the
move: when the new location does not exist yet, the legacy one is read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from hub_server.storage import LocalStorage

EVENTS_BASENAME = "events.jsonl"


def event_log_relative_path(workspace_path: str) -> str:
    """Return the current (post-M-2) events.jsonl path below one workspace."""
    return f"{workspace_path}/meta/{EVENTS_BASENAME}"


def legacy_event_log_relative_path(workspace_path: str) -> str:
    """Return the pre-M-2 events.jsonl path below one workspace."""
    return f"{workspace_path}/logs/{EVENTS_BASENAME}"


def resolve_event_log_path(storage: LocalStorage, workspace_path: str) -> Path:
    """Return the event log to read: the new location unless only legacy exists.

    A Job that started before the upgrade writes (and wrote) only below
    ``logs/``; its stream must keep working. A Job that already has events in
    ``meta/`` — the only location this Hub writes to — is read from there.
    """
    current = storage.open_relative(event_log_relative_path(workspace_path))
    if current.exists():
        return current
    legacy = storage.open_relative(legacy_event_log_relative_path(workspace_path))
    if legacy.exists():
        return legacy
    return current


def decode_event_line(raw_line: bytes | str) -> dict[str, object] | None:
    """Decode one events.jsonl line, degrading unparsable lines to ``None``.

    Legacy files may contain truncated or hand-corrupted lines (audit M-2):
    one bad line must neither take down the SSE stream nor the paginated log
    API, so decode failures and non-object payloads are skipped.
    """
    try:
        loaded = json.loads(raw_line)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if isinstance(loaded, dict):
        return cast(dict[str, object], loaded)
    return None
