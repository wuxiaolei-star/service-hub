from __future__ import annotations

import json
import logging
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hub_runner.conda_executor import CondaExecutor, RunnerBuild, RunnerJob
from python_hub_contracts import JobStatus, RunnerEvent

from deploy.runner.service_common import post_json, post_json_with_retry, retry_startup

_LOGGER = logging.getLogger(__name__)

PostFunc = Callable[[str, str, str, dict[str, Any]], dict[str, Any] | None]

_post = post_json


def main() -> int:
    base_url = os.environ["HUB_INTERNAL_BASE_URL"].rstrip("/")
    token = os.environ["HUB_RUNNER_TOKEN"]
    data_root = Path(os.environ.get("HUB_DATA_ROOT", "/data"))
    poll_interval = float(os.environ.get("HUB_RUNNER_POLL_INTERVAL_SECONDS", "2"))
    executor = CondaExecutor(data_root=data_root)
    signal.signal(signal.SIGTERM, _terminate_cleanly)
    retry_startup(lambda: _reconcile_interrupted_jobs(base_url, token))

    while True:
        operation = _post(base_url, token, "/operations/claim", {"runtime_type": "conda-pack"})
        if operation is not None:
            result = executor.install(_build_from_payload(operation["build"], operation))
            _post(
                base_url,
                token,
                f"/operations/{operation['operation_id']}/complete",
                {
                    "runtime_type": "conda-pack",
                    "status": result.status,
                    "environment_path": result.environment_path,
                    "metadata": result.metadata or {},
                    "error_summary": result.error_summary,
                    "exit_code": result.exit_code,
                },
            )
            continue

        job = _post(base_url, token, "/jobs/claim", {"runtime_type": "conda-pack"})
        if job is not None:
            runner_job = _job_from_payload(job)
            executor = CondaExecutor(
                data_root=data_root,
                event_callback=_event_forwarder(base_url, token, runner_job.id),
                cancellation_requested=_cancellation_checker(
                    base_url, token, runner_job.id
                ),
            )
            completion = executor.execute(runner_job)
            result_payload = _result_payload(data_root, runner_job, job, completion)
            # Audit M-3: a rejected completion is retried with backoff and,
            # when it still fails, logged and left to the Hub's reconcile —
            # it must never break this runner's main loop.
            post_json_with_retry(
                base_url,
                token,
                f"/jobs/{job['job_id']}/complete",
                {
                    "runtime_type": "conda-pack",
                    "result": result_payload,
                    "exit_code": completion.exit_code,
                },
                post=_post,
            )
            continue

        time.sleep(poll_interval)


def _reconcile_interrupted_jobs(
    base_url: str,
    token: str,
    *,
    post: PostFunc = _post,
) -> None:
    post(base_url, token, "/jobs/reconcile", {"runtime_type": "conda-pack"})


def _terminate_cleanly(_signum: int, _frame: Any) -> None:
    raise SystemExit(0)


def _build_from_payload(build: dict[str, Any], operation: dict[str, Any]) -> RunnerBuild:
    # Audit L-13: the build manifest's conda runtime fingerprint rides the
    # claim; the executor verifies the archive against it before unpacking.
    fingerprint = build.get("runtime", {}).get("fingerprint")
    return RunnerBuild(
        id=str(build["build_id"]),
        runtime_type="conda-pack",
        runtime_archive=str(build["runtime_archive"]),
        fingerprint=str(fingerprint) if fingerprint else None,
        timeout_seconds=operation.get("timeout_seconds"),
    )


def _result_payload(
    data_root: Path,
    runner_job: RunnerJob,
    job: dict[str, Any],
    completion: Any,
) -> dict[str, Any]:
    """Load the plugin's result.json, degrading any protocol garbage (audit M-3).

    A plugin that corrupts its result.json used to raise straight out of the
    main loop and take the runner — and, through the restart reconcile, every
    other Job of this runtime — with it. Here a missing or unparsable result
    degrades to the runner-generated fallback for this one Job only.
    """
    try:
        result_path = data_root / runner_job.result_path
        payload = json.loads(result_path.read_text("utf-8")) if result_path.exists() else None
    except (OSError, ValueError) as error:
        _LOGGER.warning("job %s produced an unreadable result.json: %s", runner_job.id, error)
        payload = None
    if payload is None:
        payload = _fallback_result(job["job"]["job"]["id"], completion.status)
    return payload


def _job_from_payload(payload: dict[str, Any]) -> RunnerJob:
    workspace = str(payload["workspace"])
    paths = payload["paths"]
    build = payload["build"]
    return RunnerJob(
        id=str(payload["job_id"]),
        runtime_type="conda-pack",
        workspace=workspace,
        job_path=f"{workspace}/{paths['job']}",
        manifest_path=str(build["manifest"]),
        plugin_root=str(build["source"]),
        result_path=f"{workspace}/{paths['result']}",
        environment_path=build.get("environment_path"),
        timeout_seconds=int(payload["job"]["execution"]["timeout"]),
        cancel_requested=bool(payload["cancel_requested"]),
        memory_mb=build.get("memory_mb"),
        cpus=build.get("cpus"),
    )


def _event_forwarder(
    base_url: str,
    token: str,
    job_id: str,
) -> Callable[[RunnerEvent], None]:
    def forward(event: RunnerEvent) -> None:
        _post(
            base_url,
            token,
            f"/jobs/{job_id}/events",
            {
                "runtime_type": "conda-pack",
                "event": event.model_dump(mode="json"),
            },
        )

    return forward


def _cancellation_checker(
    base_url: str,
    token: str,
    job_id: str,
    *,
    post: PostFunc = _post,
) -> Callable[[], bool]:
    def check() -> bool:
        response = post(
            base_url,
            token,
            f"/jobs/{job_id}/cancellation",
            {"runtime_type": "conda-pack"},
        )
        return bool(response and response["cancel_requested"])

    return check


def _fallback_result(job_id: str, status: JobStatus) -> dict[str, Any]:
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    error = None
    if status == JobStatus.TIMED_OUT:
        error = {"type": "RunnerTimeout", "code": "JOB_TIMED_OUT", "message": "Job timed out"}
    elif status != JobStatus.SUCCESS:
        error = {"type": "RunnerError", "code": "RUNNER_FAILED", "message": "Runner failed"}
    return {
        "protocol_version": "1.0",
        "job_id": job_id,
        "status": status.value,
        "started_at": now,
        "finished_at": now,
        "duration_ms": 0,
        "message": "runner completed without result.json",
        "data": {},
        "files": [],
        "error": error,
    }


if __name__ == "__main__":
    raise SystemExit(main())
