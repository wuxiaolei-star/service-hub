from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from python_hub_contracts import JobResult


def write_result_atomic(path: Path, result: JobResult | Mapping[str, Any]) -> None:
    """Validate and atomically replace a job result file."""
    validated = result if isinstance(result, JobResult) else JobResult.model_validate(result)
    tmp_path = path.with_suffix(".tmp")
    payload = json.dumps(
        validated.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("w", encoding="utf-8", newline="\n") as output:
            output.write(payload)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
