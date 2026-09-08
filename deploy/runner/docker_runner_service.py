from __future__ import annotations

import json
import os
import signal
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hub_runner.docker_executor import (
    DockerExecutor,
    RunnerBuild,
    RunnerJob,
    cleanup_owned_plugin_containers,
    load_result_payload,
)
from python_hub_contracts import JobStatus, RunnerEvent

PostFunc = Callable[[str, str, str, dict[str, Any]], dict[str, Any] | None]


def main() -> int:
    import docker

    base_url = os.environ["HUB_INTERNAL_BASE_URL"].rstrip("/")
    token = os.environ["HUB_RUNNER_TOKEN"]
    data_root = Path(os.environ.get("HUB_DATA_ROOT", "/data"))
    docker_host_data_root = os.environ["HUB_DOCKER_HOST_DATA_ROOT"]
    poll_interval = float(os.environ.get("HUB_RUNNER_POLL_INTERVAL_SECONDS", "2"))
    client = docker.from_env()
    signal.signal(signal.SIGTERM, _terminate_cleanly)
    try:
        executor = DockerExecutor(
            client=client,
            data_root=data_root,
            docker_host_data_root=docker_host_data_root,
        )
        _reconcile_interrupted_jobs(
            base_url,
            token,
            client=client,
            docker_host_data_root=docker_host_data_root,
        )

        while True:
            operation = _post(base_url, token, "/operations/claim", {"runtime_type": "docker"})
            if operation is not None:
                result = executor.install(_build_from_payload(operation["build"], operation))
                _post(
                    base_url,
                    token,
                    f"/operations/{operation['operation_id']}/complete",
                    {
                        "runtime_type": "docker",
                        "status": result.status,
                        "image_digest": result.image_digest,
                        "metadata": result.metadata or {},
                        "error_summary": result.error_summary,
                        "exit_code": result.exit_code,
                    },
                )
                continue

            job = _post(base_url, token, "/jobs/claim", {"runtime_type": "docker"})
            if job is not None:
                runner_job = _job_from_payload(job)
                executor = DockerExecutor(
                    client=client,
                    data_root=data_root,
                    docker_host_data_root=docker_host_data_root,
                    event_callback=_event_forwarder(base_url, token, runner_job.id),
                    cancellation_requested=_cancellation_checker(
                        base_url, token, runner_job.id
                    ),
                )
                completion = executor.execute(runner_job)
                result_payload = load_result_payload(data_root, runner_job.result_path)
                if result_payload is None:
                    result_payload = _fallback_result(job["job"]["job"]["id"], completion.status)
                _post(
                    base_url,
                    token,
                    f"/jobs/{job['job_id']}/complete",
                    {
                        "runtime_type": "docker",
                        "result": result_payload,
                        "exit_code": completion.exit_code,
                    },
                )
                continue

            time.sleep(poll_interval)
    finally:
        cleanup_owned_plugin_containers(
            client,
            docker_host_data_root=docker_host_data_root,
        )


def _post(
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


def _reconcile_interrupted_jobs(
    base_url: str,
    token: str,
    *,
    client: Any,
    docker_host_data_root: str,
    post: PostFunc = _post,
) -> None:
    cleanup_owned_plugin_containers(
        client,
        docker_host_data_root=docker_host_data_root,
    )
    post(base_url, token, "/jobs/reconcile", {"runtime_type": "docker"})


def _terminate_cleanly(_signum: int, _frame: Any) -> None:
    raise SystemExit(0)


def _build_from_payload(build: dict[str, Any], operation: dict[str, Any]) -> RunnerBuild:
    runtime = build["runtime"]
    return RunnerBuild(
        id=str(build["build_id"]),
        runtime_type="docker",
        runtime_archive=str(build["runtime_archive"]),
        image_digest=str(runtime["digest"]),
        timeout_seconds=operation.get("timeout_seconds"),
    )


def _job_from_payload(payload: dict[str, Any]) -> RunnerJob:
    workspace = str(payload["workspace"])
    paths = payload["paths"]
    build = payload["build"]
    return RunnerJob(
        id=str(payload["job_id"]),
        runtime_type="docker",
        workspace=workspace,
        job_path=f"{workspace}/{paths['job']}",
        result_path=f"{workspace}/output/{paths['result']}",
        image_digest=build.get("image_digest"),
        timeout_seconds=int(payload["job"]["execution"]["timeout"]),
        cancel_requested=bool(payload["cancel_requested"]),
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
                "runtime_type": "docker",
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
            {"runtime_type": "docker"},
        )
        return bool(response and response["cancel_requested"])

    return check


def _fallback_result(job_id: str, status: JobStatus) -> dict[str, Any]:
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
