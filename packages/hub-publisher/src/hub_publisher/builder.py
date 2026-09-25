"""Runtime builders for generic Service Hub plugin projects."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Protocol

from python_hub_contracts import PluginBuildManifest

from .archive import (
    compress_zstd,
    compress_zstd_stream,
    create_plugin_package,
    sha256_file,
    zstd_level,
)
from .project import (
    Architecture,
    PluginProject,
    RuntimeType,
    validate_architecture,
    validate_runtime_type,
)

HUB_PACKAGES = ("hub-contracts", "hub-sdk", "hub-runner")
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]

# A docker build plus its ~1 GB `docker save` + zstd pass costs minutes even with warm
# layer caches. When neither the plugin sources, the Dockerfile, the hub wheels, the
# base image nor the build settings changed, the bytes would come out identical, so the
# finished package is kept and reused keyed by a hash over exactly those inputs.
# Bump PACKAGE_CACHE_FORMAT whenever packaging itself changes output bytes.
PACKAGE_CACHE_FORMAT = 1
_CACHE_DISABLED_VALUES = {"0", "false", "no", "off"}
_CACHE_KEEP_ENTRIES = 3


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
        stream: Callable[[IO[bytes]], None] | None = None,
    ) -> CommandResult:
        """Run a command, optionally spooling stdout to a file or into a consumer."""


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
    if selected_runtime == "docker":
        cached = _cached_docker_package(project, selected_arch, output_dir, runner)
        if cached is not None:
            return cached
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
        destination = create_plugin_package(project, build, runtime_archive, output_dir, epoch)
    if selected_runtime == "docker":
        _store_docker_package_cache(project, selected_arch, output_dir, runner, destination)
    return destination


def _package_cache_dir(output_dir: Path) -> Path | None:
    """Return the package cache directory, or None when the cache is disabled.

    The cache lives next to the output by default (typically the deployment host's
    plugin-out directory, which persists across builds); HUB_PLUGIN_CACHE_DIR moves
    it and HUB_PLUGIN_CACHE=0 turns it off entirely.
    """
    if os.environ.get("HUB_PLUGIN_CACHE", "").strip().lower() in _CACHE_DISABLED_VALUES:
        return None
    configured = os.environ.get("HUB_PLUGIN_CACHE_DIR", "").strip()
    return Path(configured) if configured else output_dir / ".pypkg-cache"


def _tree_digest(root: Path) -> str:
    """Return a deterministic SHA256 over every regular file below ``root``."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _dockerfile_base_image(dockerfile: Path) -> str:
    """Return the image reference of the first FROM instruction."""
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("FROM"):
            # FROM [--platform=<...>] <image> [AS <stage>]
            for token in stripped.split()[1:]:
                if not token.startswith("--"):
                    return token
            break
    raise ValueError("Dockerfile has no FROM instruction")


def _base_image_id(base_image: str, runner: CommandRunner) -> str:
    """Identify the local base image so a base-image update invalidates the cache.

    A missing base image is its own key component: the next build has to pull it,
    so the produced bytes may differ from anything cached so far.
    """
    try:
        return runner(
            ["docker", "image", "inspect", "--format", "{{.Id}}", base_image],
            capture_stdout=True,
        ).stdout.strip() or "missing"
    except (subprocess.CalledProcessError, OSError):
        return "missing"


def _docker_package_cache_key(
    project: PluginProject,
    arch: Architecture,
    dockerfile: Path,
    base_image_id: str,
) -> str:
    """Hash every input that can change the produced package bytes."""
    digest = hashlib.sha256()
    digest.update(f"hub-publisher-package-cache-v{PACKAGE_CACHE_FORMAT}\0".encode())
    # plugin.yaml pins the version and entrypoint the package metadata carries.
    digest.update(project.manifest_path.read_bytes())
    digest.update(b"\0")
    digest.update(project.source_digest().encode())
    digest.update(b"\0")
    digest.update(dockerfile.read_bytes())
    digest.update(b"\0")
    # The hub wheels are built from these trees and installed into the image.
    for package in HUB_PACKAGES:
        digest.update(_tree_digest(REPOSITORY_ROOT / "packages" / package).encode())
        digest.update(b"\0")
    digest.update(base_image_id.encode())
    digest.update(b"\0")
    # Mirrors and the compression level are the remaining knobs: they do not
    # change what is installed, but they do decide the bytes on disk.
    digest.update(os.environ.get("HUB_PLUGIN_PIP_INDEX_URL", "").encode())
    digest.update(b"\0")
    digest.update(os.environ.get("HUB_PLUGIN_APT_MIRROR", "").encode())
    digest.update(b"\0")
    digest.update(str(zstd_level()).encode())
    digest.update(b"\0")
    digest.update(arch.encode())
    return digest.hexdigest()


def _package_file_name(project: PluginProject, arch: Architecture) -> str:
    plugin = project.manifest.plugin
    return f"{plugin.id}-{plugin.version}-linux-{arch}-docker.pypkg"


def _cache_paths(
    project: PluginProject,
    arch: Architecture,
    output_dir: Path,
    runner: CommandRunner,
) -> tuple[Path, Path]:
    """Return the cache entry and package paths for this build's inputs."""
    cache_dir = _package_cache_dir(output_dir)
    assert cache_dir is not None
    dockerfile = (
        project.dockerfile
        if project.dockerfile is not None
        else project.validate_runtime("docker")
    )
    base_image_id = _base_image_id(_dockerfile_base_image(dockerfile), runner)
    key = _docker_package_cache_key(project, arch, dockerfile, base_image_id)
    return cache_dir / f"{key}.json", cache_dir / f"{key}.pypkg"


def _cached_docker_package(
    project: PluginProject,
    arch: Architecture,
    output_dir: Path,
    runner: CommandRunner,
) -> Path | None:
    """Return the reused package for unchanged inputs, or None to build afresh.

    Only the docker runtime is cached: a conda-pack build depends on host state
    (conda version, channel state) that no local key can pin.
    """
    cache_dir = _package_cache_dir(output_dir)
    if cache_dir is None:
        return None
    entry_file, package_file = _cache_paths(project, arch, output_dir, runner)
    if not (entry_file.is_file() and package_file.is_file()):
        return None
    try:
        entry = json.loads(entry_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    recorded = entry.get("package_sha256") if isinstance(entry, dict) else None
    # A torn or tampered cache must never masquerade as a build result.
    if not isinstance(recorded, str) or sha256_file(package_file) != recorded:
        return None
    destination = output_dir / _package_file_name(project, arch)
    shutil.copyfile(package_file, destination)
    print(f"package cache hit: {entry_file.stem} -> {destination}")
    return destination


def _store_docker_package_cache(
    project: PluginProject,
    arch: Architecture,
    output_dir: Path,
    runner: CommandRunner,
    destination: Path,
) -> None:
    """Keep the freshly built package for the next unchanged-inputs build."""
    cache_dir = _package_cache_dir(output_dir)
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    entry_file, package_file = _cache_paths(project, arch, output_dir, runner)
    # The package lands before the entry that references it, so a crash in between
    # only wastes disk; the entry file alone would look like a cache hit.
    temporary = package_file.with_name(f".{package_file.name}.tmp")
    shutil.copyfile(destination, temporary)
    os.replace(temporary, package_file)
    entry_file.write_text(
        json.dumps(
            {
                "cache_format": PACKAGE_CACHE_FORMAT,
                "plugin_id": project.manifest.plugin.id,
                "plugin_version": project.manifest.plugin.version,
                "arch": arch,
                "source_sha256": project.source_digest(),
                "package_sha256": sha256_file(package_file),
                "package": package_file.name,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _prune_package_cache(cache_dir)


def _prune_package_cache(cache_dir: Path) -> None:
    """Keep only the newest entries so reused output dirs do not grow without bound."""
    entries = sorted(cache_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for stale in entries[_CACHE_KEEP_ENTRIES:]:
        stale.unlink(missing_ok=True)
        stale.with_suffix(".pypkg").unlink(missing_ok=True)


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
    # Same for the distro archive: the apt layer is the other slow download, spending
    # ~350 s per package against the default mirror on the reference host.
    apt_mirror = os.environ.get("HUB_PLUGIN_APT_MIRROR", "").strip()
    if apt_mirror:
        command += ["--build-arg", f"APT_MIRROR={apt_mirror}"]
    command += ["--tag", image, str(context)]
    runner(command)
    digest = runner(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_stdout=True,
    ).stdout.strip()
    # Stream docker save straight into the zstd frame: spooling the ~1GB tar first
    # costs a full write plus a full read of it.
    archive = temporary / "image.tar.zst"
    runner(
        ["docker", "image", "save", image],
        stream=lambda source: compress_zstd_stream(source, archive),
    )
    return archive, _sha256_value(digest.removeprefix("sha256:"))


def _run_command(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    stdout: Path | None = None,
    capture_stdout: bool = False,
    stream: Callable[[IO[bytes]], None] | None = None,
) -> CommandResult:
    if stream is not None:
        with subprocess.Popen(command, env=env, stdout=subprocess.PIPE) as process:
            assert process.stdout is not None
            try:
                stream(process.stdout)
            except BaseException:
                process.kill()
                raise
            returncode = process.wait()
        # A failed producer must not leave its truncated output committed.
        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, command)
        return CommandResult(stdout="")
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
