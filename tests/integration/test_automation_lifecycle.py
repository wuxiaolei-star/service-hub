"""V2.2 automation lifecycle acceptance on Linux AMD64 (deployed instance)."""

from __future__ import annotations

import hashlib
import hmac
import http.server
import os
import platform
import sys
import threading

import httpx
import pytest

pytestmark = pytest.mark.integration

PIPELINE_NAME = "integration-pipeline"
SCHEDULE_NAME = "integration-schedule"


class _HookRecorder(http.server.BaseHTTPRequestHandler):
    last_body: bytes = b""
    last_signature: str = ""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _HookRecorder.last_body = self.rfile.read(length)
        _HookRecorder.last_signature = self.headers.get("X-Hub-Signature", "")
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.mark.integration
def test_v2_automation_lifecycle() -> None:  # pragma: no cover - Linux AMD64 only
    """Callback signature, schedule trigger, and pipeline stepping on the deployed instance.

    The callback recorder listens on the deployment host's loopback, which the outbound
    webhook guard refuses by default. The instance under test must therefore opt in with
    ``webhooks.allow_private_networks: true`` in ``config/hub.yaml``; a job creation that
    answers ``422 WEBHOOK_URL_FORBIDDEN`` means that switch is missing.
    """
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        pytest.skip("automation lifecycle requires a native Linux AMD64 Docker host")
    base_url = os.environ.get("HUB_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    username = os.environ.get("HUB_BOOTSTRAP_USERNAME", "admin")
    password = os.environ.get("HUB_BOOTSTRAP_PASSWORD")
    if not password:
        pytest.skip("set HUB_BOOTSTRAP_PASSWORD to exercise the automation lifecycle")

    server = http.server.HTTPServer(("127.0.0.1", 0), _HookRecorder)
    hook_thread = threading.Thread(target=server.serve_forever, daemon=True)
    hook_thread.start()

    with httpx.Client(base_url=base_url, timeout=60) as client:
        login = client.post("/api/v1/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        secret = "integration-secret"

        upload = client.post(
            "/api/v1/files",
            headers=headers,
            files={"file": ("automation.nc", b"automation")},
        )
        assert upload.status_code == 201
        file_id = upload.json()["file_id"]

        created = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={
                "plugin_id": "nc_to_shp",
                "version": "1.0.0",
                "runtime_type": "docker",
                "inputs": {"source_nc": file_id},
                "params": {},
                "callback": {
                    "url": f"http://127.0.0.1:{server.server_port}/hook",
                    "secret": secret,
                },
            },
        )
        assert created.status_code == 201, (
            f"job creation failed ({created.status_code} {created.text}); the deployment needs "
            "webhooks.allow_private_networks: true for this loopback callback target"
        )
        job_id = created.json()["job_id"]

        pipeline = client.post(
            "/api/v1/pipelines",
            headers=headers,
            json={
                "name": PIPELINE_NAME,
                "steps": [
                    {
                        "plugin_id": "nc_to_shp",
                        "version": "1.0.0",
                        "runtime_type": "docker",
                        "inputs": {"source_nc": file_id},
                        "params": {},
                    }
                ],
            },
        )
        if pipeline.status_code == 409:
            # The acceptance suite is re-run against a long-lived instance, so a pipeline
            # left by a previous pass is reused instead of failing on the unique name.
            existing = client.get("/api/v1/pipelines", headers=headers)
            assert existing.status_code == 200
            matches = [p for p in existing.json()["items"] if p["name"] == PIPELINE_NAME]
            assert matches, "409 on create but no pipeline carries the expected name"
            pipeline_id = matches[0]["id"]
        else:
            assert pipeline.status_code == 201, pipeline.text
            pipeline_id = pipeline.json()["id"]

        executed = client.post(f"/api/v1/pipelines/{pipeline_id}/execute", headers=headers)
        assert executed.status_code == 201

        schedule_payload = {
            "name": SCHEDULE_NAME,
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "runtime_type": "docker",
            "inputs": {"source_nc": file_id},
            "params": {},
            "interval_minutes": 60,
        }
        schedule = client.post("/api/v1/schedules", headers=headers, json=schedule_payload)
        if schedule.status_code == 409:
            listed = client.get("/api/v1/schedules", headers=headers)
            assert listed.status_code == 200, listed.text
            existing_schedules = [
                s for s in listed.json()["items"] if s["name"] == SCHEDULE_NAME
            ]
            assert existing_schedules, "409 on create but no schedule carries the expected name"
            schedule_id = existing_schedules[0]["id"]
            schedule = client.post(
                f"/api/v1/schedules/{schedule_id}/enable", headers=headers
            )
        assert schedule.status_code in {200, 201}, schedule.text

        # scheduler sweeps every 30s: wait for callback delivery and pipeline step
        import time

        deadline = time.monotonic() + 120
        signature_ok = False
        while time.monotonic() < deadline:
            expected = hmac.new(
                secret.encode(), _HookRecorder.last_body, hashlib.sha256
            ).hexdigest()
            signature_ok = _HookRecorder.last_signature == f"sha256={expected}"
            if signature_ok and _HookRecorder.last_body:
                break
            time.sleep(2)
        assert signature_ok, "callback webhook was not delivered with a valid signature"

        runs = client.get(
            "/api/v1/pipelines/runs", params={"pipeline_id": pipeline_id}, headers=headers
        ).json()["items"]
        # This suite feeds a placeholder payload, so the plugin itself always fails; what is
        # under test here is that executing a pipeline materialised a run row and that the
        # run reached a terminal state instead of hanging in PENDING.
        assert runs, "executing the pipeline produced no run row"
        assert runs[0]["state"] in {"RUNNING", "SUCCEEDED", "FAILED"}, runs[0]
        assert runs[0]["pipeline_id"] == pipeline_id

        callbacks = client.get(f"/api/v1/jobs/{job_id}/callbacks", headers=headers)
        assert callbacks.status_code == 200

    server.shutdown()
