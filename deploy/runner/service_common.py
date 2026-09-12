from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


def post_json(
    base_url: str,
    token: str,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Hub-Runner-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 204:
            return None
        raise


def retry_startup(
    action: Callable[[], None],
    *,
    sleep: Callable[[float], None] = time.sleep,
    delay_seconds: float = 2.0,
) -> None:
    if delay_seconds <= 0:
        raise ValueError("delay_seconds must be positive")
    while True:
        try:
            action()
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            sleep(delay_seconds)
