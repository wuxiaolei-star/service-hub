from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

_LOGGER = logging.getLogger(__name__)


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


def post_json_with_retry(
    base_url: str,
    token: str,
    path: str,
    payload: dict[str, Any],
    *,
    post: Callable[[str, str, str, dict[str, Any]], dict[str, Any] | None] = post_json,
    attempts: int = 3,
    backoff_seconds: float = 1.0,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any] | None:
    """POST one completion report with bounded retries (audit M-3).

    A transient 5xx on the terminal completion used to kill the runner's main
    loop; on restart the reconcile pass then failed every other Job of the
    same runtime. Only ``HTTPError`` responses are retried — anything else is
    not an HTTP rejection — and after the final attempt the failure is logged
    and the caller continues (the Hub's reconcile classifies the Job).
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    pause = sleep or time.sleep
    for attempt in range(1, attempts + 1):
        try:
            return post(base_url, token, path, payload)
        except urllib.error.HTTPError as error:
            if attempt >= attempts:
                _LOGGER.error(
                    "completion POST to %s failed after %d attempts: %s",
                    path,
                    attempts,
                    error,
                )
                return None
            _LOGGER.warning(
                "completion POST to %s failed (attempt %d/%d): %s",
                path,
                attempt,
                attempts,
                error,
            )
            pause(backoff_seconds * attempt)
    return None


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
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            sleep(delay_seconds)
