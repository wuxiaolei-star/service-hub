"""Runtime builders for generic Service Hub plugin projects."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from python_hub_contracts import PluginBuildManifest

from .archive import compress_zstd, create_plugin_package, sha256_file
from .project import (
    Architecture,
    PluginProject,
    RuntimeType,
    validate_architecture,
    validate_runtime_type,
)

HUB_PACKAGES = ("hub-contracts", "hub-sdk", "hub-runner")
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str = ""


class CommandRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        stdout: Path | None = None,
        capture_stdout: bool = False,
    ) -> CommandResult:
        """Run a command and optionally stream stdout to a file."""


def build_plugin(
    project: PluginProject,
    runtime_type: RuntimeType,
    arch: Architecture,
    output_dir: Path,
    *,
    command_runner: CommandRunner | None = None,
    source_date_epoch: int | None = None,
) -> Path:
    """Build or consume a runtime, then write a `.pypkg` package."""
    selected_runtime = validate_runtime_type(runtime_type)
    selected_arch = validate_architecture(arch)
    epoch = _source_date_epoch(source_date_epoch)
    runner = command_runner or _run_command
    output_dir = output_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="hub-plugin-build-") as temporary_name:
        temporary = Path(temporary_name)
        runtime_archive, docker_digest = _build_runtime(
            project,
            selected_runtime,
            selected_arch,
            temporary,
            runner,
        )
        build = create_build_manifest(
            project,
            selected_runtime,
            selected_arch,
            runtime_archive,
            docker_digest=docker_digest,
            source_date_epoch=epoch,
        )
        return create_plugin_package(project, build, runtime_archive, output_dir, epoch)


def create_build_manifest(
    project: PluginProject,
    runtime_type: RuntimeType,
    arch: Architecture,
    runtime_archive: Path,
    *,
    docker_digest: str | None,
    source_date_epoch: int,
) -> PluginBuildManifest:
    """Create build metadata for an already prepared runtime archive."""
    runtime_sha256 = sha256_file(runtime_archive)
    if runtime_type == "docker":
        if docker_digest is None:
            raise ValueError("docker builds require the loaded image SHA256 digest")
        docker_digest = _sha256_value(docker_digest)
        fingerprint = docker_digest
        runtime_data: dict[str, str] = {
            "type": "docker",
            "archive": "image.tar.zst",
            "image": f"{project.manifest.plugin.id}:{project.manifest.plugin.version}-linux-{arch}",
            "digest": docker_digest,
        }
    else:
        if docker_digest is not None:
            raise ValueError("docker_digest is only valid for Docker builds")
        fingerprint = runtime_sha256
        runtime_data = {
            "type": "conda-pack",
            "archive": "runtime/env.tar.zst",
            "fingerprint": runtime_sha256,
        }
    build_id = (
        f"{project.manifest.plugin.id}-{project.manifest.plugin.version}-linux-"
        f"{arch}-{runtime_type}-{fingerprint[:12]}"
    )
    build = PluginBuildManifest.model_validate(
        {
            "schema_version": "1.0",
            "build_id": build_id,
            "plugin_id": project.manifest.plugin.id,
            "plugin_version": project.manifest.plugin.version,
            "target": {"os": "linux", "arch": arch},
            "python_version": project.manifest.runtime.python.version,
            "runtime": runtime_data,
            "sdk_version": "1.0.0",
            "source_sha256": project.source_digest(),
            "built_at": datetime.fromtimestamp(source_date_epoch, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
        }
    )
    build.assert_matches_plugin(project.manifest)
    return build


def _build_runtime(
    project: PluginProject,
    runtime_type: RuntimeType,
    arch: Architecture,
    temporary: Path,
    runner: CommandRunner,
) -> tuple[Path, str | None]:
    wheelhouse = temporary / "wheelhouse"
    _build_hub_wheels(wheelhouse, runner)
    if runtime_type == "conda-pack":
        return _build_conda_runtime(project, arch, temporary, wheelhouse, runner), None
    return _build_docker_runtime(project, arch, temporary, wheelhouse, runner)


def _build_hub_wheels(wheelhouse: Path, runner: CommandRunner) -> None:
    wheelhouse.mkdir()
    for package in HUB_PACKAGES:
        runner(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--wheel-dir",
                str(wheelhouse),
                str(REPOSITORY_ROOT / "packages" / package),
            ]
        )


def _build_conda_runtime(
    project: PluginProject,
    arch: Architecture,
    temporary: Path,
    wheelhouse: Path,
    runner: CommandRunner,
) -> Path:
    environment_file = project.validate_runtime("conda-pack")
    if _host_linux_architecture() != arch:
        raise RuntimeError(
            "Conda-Pack runtime must be built natively on the target Linux architecture"
        )
    environment = temporary / "environment"
    env = {**os.environ, "PIP_FIND_LINKS": str(wheelhouse), "PIP_NO_INDEX": "1"}
    runner(
        [
            "conda",
            "env",
            "create",
            "--prefix",
            str(environment),
            "--file",
            str(environment_file),
            "--yes",
        ],
        env=env,
    )
    raw_archive = temporary / "env.tar"
    runner(
        [
            "conda",
            "run",
            "--prefix",
            str(environment),
            "conda-pack",
            "--prefix",
            str(environment),
            "--output",
            str(raw_archive),
        ]
    )
    archive = temporary / "env.tar.zst"
    compress_zstd(raw_archive, archive)
    return archive


def _build_docker_runtime(
    project: PluginProject,
    arch: Architecture,
    temporary: Path,
    wheelhouse: Path,
    runner: CommandRunner,
) -> tuple[Path, str]:
    dockerfile = project.validate_runtime("docker")
    context = temporary / "docker-context"
    shutil.copytree(wheelhouse, context / "wheelhouse")
    shutil.copytree(project.source_dir, context / "plugin")
    shutil.copy2(project.manifest_path, context / "plugin.yaml")
    shutil.copy2(dockerfile, context / "Dockerfile")
    image = f"{project.manifest.plugin.id}:{project.manifest.plugin.version}-linux-{arch}"
    docker_platform = "linux/amd64" if arch == "amd64" else "linux/arm64"
    command = [
        "docker",
        "build",
        "--platform",
        docker_platform,
        "--build-arg",
        f"SOURCE_SHA256={project.source_digest()}",
    ]
    # Pass the optional PyPI mirror through to the Dockerfile. Unset, the build arg stays
    # empty and pip keeps its default index, so an offline build is unaffected. Setting
    # HUB_PLUGIN_PIP_INDEX_URL lets a build reach a nearer mirror, which on a slow
    # international link is the difference between about a minute and about an hour.
    pip_index_url = os.environ.get("HUB_PLUGIN_PIP_INDEX_URL", "").strip()
    if pip_index_url:
        command += ["--build-arg", f"PIP_INDEX_URL={pip_index_url}"]
    command += ["--tag", image, str(context)]
    runner(command)
    digest = runner(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_stdout=True,
    ).stdout.strip()
    raw_archive = temporary / "image.tar"
    runner(["docker", "image", "save", image], stdout=raw_archive)
    archive = temporary / "image.tar.zst"
    compress_zstd(raw_archive, archive)
    return archive, _sha256_value(digest.removeprefix("sha256:"))


def _run_command(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    stdout: Path | None = None,
    capture_stdout: bool = False,
) -> CommandResult:
    if stdout is None:
        result = subprocess.run(
            command,
            check=True,
            env=env,
            text=capture_stdout,
            stdout=subprocess.PIPE if capture_stdout else None,
        )
        return CommandResult(stdout=result.stdout if capture_stdout else "")
    with stdout.open("wb") as output:
        subprocess.run(command, check=True, env=env, stdout=output)
    return CommandResult(stdout="")


def _host_linux_architecture() -> Architecture:
    if sys.platform != "linux":
        raise RuntimeError("Conda-Pack runtime must be built on Linux")
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    raise RuntimeError(f"unsupported Linux architecture: {machine}")


def _sha256_value(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("Docker image digest must be a 64-character SHA256")
    return normalized


def _source_date_epoch(explicit: int | None) -> int:
    value = explicit if explicit is not None else int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    if value < 0:
        raise ValueError("SOURCE_DATE_EPOCH must be non-negative")
    return value
