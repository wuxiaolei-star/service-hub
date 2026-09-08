from __future__ import annotations

import json
import os
import platform
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPOSITORY_ROOT / "packages" / "nc-to-shp-plugin"
PLUGIN_ID = "nc_to_shp"
PLUGIN_VERSION = "1.0.0"


@pytest.mark.integration
def test_amd64_conda_and_docker_nc_jobs_are_semantically_equal(tmp_path: Path) -> None:
    """Install both AMD64 Builds, run the same NC input twice, and compare outputs."""
    sample_value = os.environ.get("HUB_NC_SAMPLE_PATH")
    if not sample_value:
        pytest.skip("set HUB_NC_SAMPLE_PATH to run the external Linux AMD64 acceptance")
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        pytest.skip("dual-runtime acceptance requires a native Linux AMD64 Docker host")
    sample = Path(sample_value).resolve()
    assert sample.is_file(), f"HUB_NC_SAMPLE_PATH does not exist: {sample}"
    package_dir = Path(
        os.environ.get("HUB_PLUGIN_PACKAGE_DIR", "packages/nc-to-shp-plugin/dist")
    ).resolve()
    conda_package = package_dir / "nc_to_shp-1.0.0-linux-amd64-conda.pypkg"
    docker_package = package_dir / "nc_to_shp-1.0.0-linux-amd64-docker.pypkg"
    assert conda_package.is_file(), f"missing Conda package: {conda_package}"
    assert docker_package.is_file(), f"missing Docker package: {docker_package}"

    base_url = os.environ.get("HUB_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    with httpx.Client(
        base_url=base_url,
        timeout=_positive_timeout("HUB_ACCEPTANCE_HTTP_TIMEOUT_SECONDS", 1800),
    ) as client:
        source_file_id = _upload_file(client, sample)
        conda_build = _install_and_enable(client, conda_package)
        docker_build = _install_and_enable(client, docker_package)

        assert conda_build["runtime_type"] == "conda-pack"
        assert docker_build["runtime_type"] == "docker"

        conda_output = _run_job(client, source_file_id, "conda-pack", tmp_path)
        docker_output = _run_job(client, source_file_id, "docker", tmp_path)

    _assert_expected_archive(conda_output)
    _assert_expected_archive(docker_output)
    differences = _compare_archives(conda_output, docker_output)
    assert differences == []


def _compare_archives(left: Path, right: Path) -> list[str]:
    sys.path.insert(0, str(PLUGIN_ROOT))
    from scripts.compare_shapefile_zip import compare_archives

    return compare_archives(left, right, expected_features=39464)


def _upload_file(client: httpx.Client, sample: Path) -> str:
    with sample.open("rb") as handle:
        response = client.post(
            "/api/v1/files",
            files={"file": (sample.name, handle, "application/octet-stream")},
        )
    response.raise_for_status()
    return str(response.json()["file_id"])


def _install_and_enable(client: httpx.Client, package: Path) -> dict[str, Any]:
    with package.open("rb") as handle:
        installed = client.post(
            "/api/v1/plugins/install",
            files={"file": (package.name, handle, "application/octet-stream")},
        )
    installed.raise_for_status()
    build_id = str(installed.json()["build_id"])
    ready = _wait_for_build(client, build_id)
    if ready["status"] != "ENABLED":
        enabled = client.post(f"/api/v1/plugin-builds/{build_id}/enable")
        enabled.raise_for_status()
        return dict(enabled.json())
    return ready


def _wait_for_build(client: httpx.Client, build_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + _positive_timeout(
        "HUB_ACCEPTANCE_BUILD_TIMEOUT_SECONDS", 1800
    )
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/plugin-builds/{build_id}")
        response.raise_for_status()
        payload = dict(response.json())
        if payload["status"] in {"READY", "ENABLED"}:
            return payload
        if payload["status"] == "FAILED":
            raise AssertionError(f"Build failed: {payload}")
        time.sleep(2)
    raise AssertionError(f"Build did not become READY: {build_id}")


def _run_job(
    client: httpx.Client,
    source_file_id: str,
    runtime_type: str,
    tmp_path: Path,
) -> Path:
    created = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": PLUGIN_ID,
            "version": PLUGIN_VERSION,
            "runtime_type": runtime_type,
            "inputs": {"source_nc": source_file_id},
            "params": {
                "group_name": "1",
                "metrics": ["depth", "stage"],
                "start_time": 1,
                "end_time": 20,
                "target_crs": None,
            },
        },
    )
    created.raise_for_status()
    job_id = str(created.json()["job_id"])
    _wait_for_success(client, job_id)
    logs = client.get(f"/api/v1/jobs/{job_id}/logs", params={"cursor": 0, "limit": 500})
    logs.raise_for_status()
    assert logs.json()["items"], f"Job emitted no runner events: {job_id}"
    outputs = client.get(f"/api/v1/jobs/{job_id}/outputs")
    outputs.raise_for_status()
    items = outputs.json()["items"]
    assert len(items) == 1
    archive = tmp_path / f"{runtime_type}.zip"
    download = client.get(f"/api/v1/files/{items[0]['file_id']}/download")
    download.raise_for_status()
    archive.write_bytes(download.content)
    return archive


def _wait_for_success(client: httpx.Client, job_id: str) -> None:
    deadline = time.monotonic() + _positive_timeout(
        "HUB_ACCEPTANCE_JOB_TIMEOUT_SECONDS", 3900
    )
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] == "SUCCESS":
            return
        if payload["status"] in {"FAILED", "CANCELLED", "TIMED_OUT"}:
            raise AssertionError(f"Job ended unexpectedly: {payload}")
        time.sleep(2)
    raise AssertionError(f"Job did not finish: {job_id}")


def _assert_expected_archive(archive: Path) -> None:
    expected_components = {
        *(f"depth_1-20.{suffix}" for suffix in ("shp", "shx", "dbf", "prj")),
        *(f"stage_1-20.{suffix}" for suffix in ("shp", "shx", "dbf", "prj")),
        "manifest.json",
    }
    with zipfile.ZipFile(archive) as result_zip:
        assert set(result_zip.namelist()) == expected_components
        manifest = json.loads(result_zip.read("manifest.json"))
    assert manifest["feature_count"] == 39464
    assert manifest["parameters"] == {
        "group_name": "1",
        "metrics": ["depth", "stage"],
        "start_time": 1,
        "end_time": 20,
        "target_crs": None,
    }
    assert manifest["crs"] == "EPSG:4326"


def _positive_timeout(name: str, default: int) -> float:
    value = float(os.environ.get(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value
