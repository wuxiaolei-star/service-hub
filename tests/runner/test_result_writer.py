from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hub_runner.result_writer import write_result_atomic
from python_hub_contracts import JobError, JobResult, JobStatus


def test_write_result_atomic_replaces_existing_file_with_validated_json(tmp_path: Path) -> None:
    """A direct or partial write would leave stale/corrupt result.json visible."""
    result_path = tmp_path / "result.json"
    result_path.write_text("stale", encoding="utf-8")
    started_at = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)
    result = JobResult(
        protocol_version="1.0",
        job_id="job_123",
        status=JobStatus.SUCCESS,
        started_at=started_at,
        finished_at=started_at,
        duration_ms=0,
        message="complete",
        data={"ok": True},
        files=[],
        error=None,
    )

    write_result_atomic(result_path, result)

    payload = json.loads(result_path.read_text("utf-8"))
    assert payload["job_id"] == "job_123"
    assert payload["status"] == "SUCCESS"
    assert not result_path.with_suffix(".tmp").exists()
    assert JobResult.model_validate(payload).status is JobStatus.SUCCESS


def test_write_result_atomic_rejects_invalid_result_without_replacing_existing_file(
    tmp_path: Path,
) -> None:
    """Skipping validation before replacement could persist invalid terminal failures."""
    result_path = tmp_path / "result.json"
    result_path.write_text("original", encoding="utf-8")
    started_at = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)

    with pytest.raises(ValueError):
        write_result_atomic(
            result_path,
            {
                "protocol_version": "1.0",
                "job_id": "job_123",
                "status": "TIMED_OUT",
                "started_at": started_at.isoformat(),
                "finished_at": started_at.isoformat(),
                "duration_ms": 0,
                "message": "timeout",
                "data": {},
                "files": [],
                "error": JobError(
                    type="runtime",
                    code="WRONG_CODE",
                    message="wrong",
                ).model_dump(mode="json"),
            },
        )

    assert result_path.read_text("utf-8") == "original"
    assert not result_path.with_suffix(".tmp").exists()
