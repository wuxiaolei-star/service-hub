"""Publisher-side reproducible builders for Conda-Pack and Docker `.pypkg` files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, cast

import zstandard
from python_hub_contracts import PluginBuildManifest, load_plugin_manifest

RuntimeType = Literal["conda-pack", "docker"]
Architecture = Literal["amd64", "arm64"]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parents[1]
PLUGIN_SOURCE = PACKAGE_ROOT / "src" / "nc_to_shp_plugin"
PLUGIN_MANIFEST = PACKAGE_ROOT / "plugin.yaml"
HUB_PACKAGES = ("hub-contracts", "hub-sdk", "hub-runner")
CHUNK_SIZE = 1024 * 1024


def build_package(
    *,
    runtime_type: str,
    arch: str,
    output_dir: Path,
    prepared_runtime: Path | None = None,
    docker_digest: str | None = None,
    source_date_epoch: int | None = None,
) -> Path:
    """Build or consume a target runtime, then create one immutable plugin package."""
    selected_runtime = _runtime_type(runtime_type)
    selected_arch = _architecture(arch)
    epoch = _source_date_epoch(source_date_epoch)
    manifest = load_plugin_manifest(PLUGIN_MANIFEST)
    source_files = list(_source_files())
    source_sha256 = _source_digest(source_files)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="nc-to-shp-build-") as temporary_name:
        temporary = Path(temporary_name)
        if prepared_runtime is None:
            runtime_archive, resolved_docker_digest = _build_runtime(
                selected_runtime, selected_arch, temporary, source_sha256
            )
        else:
            runtime_archive = prepared_runtime.resolve(strict=True)
            resolved_docker_digest = docker_digest
        runtime_sha256 = _sha256(runtime_archive)
        if selected_runtime == "docker":
            if resolved_docker_digest is None:
                raise ValueError("docker builds require the loaded image SHA256 digest")
            resolved_docker_digest = _sha256_value(resolved_docker_digest)
        elif docker_digest is not None:
            raise ValueError("docker_digest is only valid for Docker builds")

        runtime_path = (
            "runtime/env.tar.zst" if selected_runtime == "conda-pack" else "image.tar.zst"
        )
        fingerprint = runtime_sha256 if selected_runtime == "conda-pack" else resolved_docker_digest
        build_id = (
            f"nc_to_shp-1.0.0-linux-{selected_arch}-{selected_runtime}-"
            f"{cast(str, fingerprint)[:12]}"
        )
        runtime_data: dict[str, str] = {
            "type": selected_runtime,
            "archive": runtime_path,
        }
        if selected_runtime == "conda-pack":
            runtime_data["fingerprint"] = runtime_sha256
        else:
            runtime_data.update(
                {
                    "image": f"nc_to_shp:1.0.0-linux-{selected_arch}",
                    "digest": cast(str, resolved_docker_digest),
                }
            )
        built_at = datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")
        build_data = {
            "schema_version": "1.0",
            "build_id": build_id,
            "plugin_id": manifest.plugin.id,
            "plugin_version": manifest.plugin.version,
            "target": {"os": "linux", "arch": selected_arch},
            "python_version": manifest.runtime.python.version,
            "runtime": runtime_data,
            "sdk_version": "1.0.0",
            "source_sha256": source_sha256,
            "built_at": built_at,
        }
        build = PluginBuildManifest.model_validate(build_data)
        build.assert_matches_plugin(manifest)
        files: dict[str, Path | bytes] = {
            "plugin.yaml": PLUGIN_MANIFEST,
            "build.json": _json_bytes(build.model_dump(mode="json")),
            runtime_path: runtime_archive,
        }
        for source_file in source_files:
            relative = source_file.relative_to(PLUGIN_SOURCE).as_posix()
            files[f"plugin/nc_to_shp_plugin/{relative}"] = source_file
        checksums = {name: _content_sha256(content) for name, content in files.items()}
        files["checksums.json"] = _json_bytes(checksums)

        runtime_label = "conda" if selected_runtime == "conda-pack" else "docker"
        destination = output_dir / (
            f"nc_to_shp-1.0.0-linux-{selected_arch}-{runtime_label}.pypkg"
        )
        raw_tar = temporary / "package.tar"
        directories = {"plugin", "plugin/nc_to_shp_plugin"}
        if selected_runtime == "conda-pack":
            directories.add("runtime")
        _write_tar(raw_tar, files, directories, epoch)
        _compress_zstd(raw_tar, destination)
    return destination


def _build_runtime(
    runtime_type: RuntimeType,
    arch: Architecture,
    temporary: Path,
    source_sha256: str,
) -> tuple[Path, str | None]:
    wheelhouse = temporary / "wheelhouse"
    _build_hub_wheels(wheelhouse)
    if runtime_type == "conda-pack":
        return _build_conda_runtime(arch, temporary, wheelhouse), None
    return _build_docker_runtime(arch, temporary, wheelhouse, source_sha256)


def _build_hub_wheels(wheelhouse: Path) -> None:
    wheelhouse.mkdir()
    for package in HUB_PACKAGES:
        _run(
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
    arch: Architecture, temporary: Path, wheelhouse: Path
) -> Path:
    _require_native_linux(arch)
    environment = temporary / "environment"
    environment_variables = {
        **os.environ,
        "PIP_FIND_LINKS": str(wheelhouse),
        "PIP_NO_INDEX": "1",
    }
    _run(
        [
            "conda",
            "env",
            "create",
            "--prefix",
            str(environment),
            "--file",
            str(PACKAGE_ROOT / "conda" / "environment.yml"),
            "--yes",
        ],
        env=environment_variables,
    )
    raw_archive = temporary / "env.tar"
    _run(
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
    _compress_zstd(raw_archive, archive)
    return archive


def _build_docker_runtime(
    arch: Architecture,
    temporary: Path,
    wheelhouse: Path,
    source_sha256: str,
) -> tuple[Path, str]:
    context = temporary / "docker-context"
    shutil.copytree(wheelhouse, context / "wheelhouse")
    shutil.copytree(PLUGIN_SOURCE, context / "plugin" / "nc_to_shp_plugin")
    shutil.copy2(PLUGIN_MANIFEST, context / "plugin.yaml")
    shutil.copy2(PACKAGE_ROOT / "docker" / "Dockerfile", context / "Dockerfile")
    image = f"nc_to_shp:1.0.0-linux-{arch}"
    docker_platform = "linux/amd64" if arch == "amd64" else "linux/arm64"
    _run(
        [
            "docker",
            "build",
            "--platform",
            docker_platform,
            "--build-arg",
            f"SOURCE_SHA256={source_sha256}",
            "--tag",
            image,
            str(context),
        ]
    )
    digest = _run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image], capture=True
    ).strip()
    digest = _sha256_value(digest.removeprefix("sha256:"))
    raw_archive = temporary / "image.tar"
    with raw_archive.open("wb") as output:
        subprocess.run(["docker", "image", "save", image], check=True, stdout=output)
    archive = temporary / "image.tar.zst"
    _compress_zstd(raw_archive, archive)
    return archive, digest


def _source_files() -> Iterable[Path]:
    for path in sorted(PLUGIN_SOURCE.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            yield path


def _source_digest(files: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(PLUGIN_SOURCE).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _write_tar(
    destination: Path,
    files: dict[str, Path | bytes],
    directories: set[str],
    epoch: int,
) -> None:
    with tarfile.open(destination, mode="w:", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(directories):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            _normalize_tar_info(info, epoch, 0o755)
            archive.addfile(info)
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(PurePosixPath(name).as_posix())
            _normalize_tar_info(info, epoch, 0o644)
            if isinstance(content, bytes):
                import io

                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            else:
                info.size = content.stat().st_size
                with content.open("rb") as source:
                    archive.addfile(info, source)


def _normalize_tar_info(info: tarfile.TarInfo, epoch: int, mode: int) -> None:
    info.mtime = epoch
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""


def _compress_zstd(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        with (
            source.open("rb") as input_file,
            temporary.open("wb") as output_file,
            zstandard.ZstdCompressor(level=19, threads=0, write_checksum=True).stream_writer(
                output_file
            ) as compressor,
        ):
            shutil.copyfileobj(input_file, compressor, length=CHUNK_SIZE)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _content_sha256(content: Path | bytes) -> str:
    if isinstance(content, bytes):
        return hashlib.sha256(content).hexdigest()
    return _sha256(content)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    document = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return document.encode() + b"\n"


def _source_date_epoch(explicit: int | None) -> int:
    value = explicit if explicit is not None else int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    if value < 0:
        raise ValueError("SOURCE_DATE_EPOCH must be non-negative")
    return value


def _runtime_type(value: str) -> RuntimeType:
    if value not in {"conda-pack", "docker"}:
        raise ValueError("runtime_type must be conda-pack or docker")
    return cast(RuntimeType, value)


def _architecture(value: str) -> Architecture:
    if value not in {"amd64", "arm64"}:
        raise ValueError("arch must be amd64 or arm64")
    return cast(Architecture, value)


def _sha256_value(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("Docker image digest must be a 64-character SHA256")
    return normalized


def _require_native_linux(arch: Architecture) -> None:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        host_arch = "amd64"
    elif machine in {"aarch64", "arm64"}:
        host_arch = "arm64"
    else:
        host_arch = machine
    if sys.platform != "linux" or host_arch != arch:
        raise RuntimeError(
            "Conda-Pack runtime must be built natively on the target Linux architecture"
        )


def _run(
    command: Sequence[str], *, env: dict[str, str] | None = None, capture: bool = False
) -> str:
    result = subprocess.run(
        command,
        check=True,
        env=env,
        text=capture,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, choices=("conda-pack", "docker"))
    parser.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prepared-runtime", type=Path)
    parser.add_argument("--docker-digest")
    args = parser.parse_args(argv)
    package = build_package(
        runtime_type=args.runtime,
        arch=args.arch,
        output_dir=args.output,
        prepared_runtime=args.prepared_runtime,
        docker_digest=args.docker_digest,
    )
    print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
