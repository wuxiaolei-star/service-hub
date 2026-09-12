"""Deterministic `.pypkg` archive creation."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
from pathlib import Path, PurePosixPath

import zstandard
from python_hub_contracts import CondaPackRuntime, PluginBuildManifest

from .project import PluginProject

CHUNK_SIZE = 1024 * 1024


def create_plugin_package(
    project: PluginProject,
    build: PluginBuildManifest,
    runtime_archive: Path,
    output_dir: Path,
    source_date_epoch: int,
) -> Path:
    """Create one immutable, reproducible plugin package."""
    if source_date_epoch < 0:
        raise ValueError("SOURCE_DATE_EPOCH must be non-negative")
    if runtime_archive.is_symlink():
        raise ValueError("runtime archive must not be a symbolic link")
    runtime_archive = runtime_archive.resolve(strict=True)
    build.assert_matches_plugin(project.manifest)
    _verify_build_matches_sources(build, project, runtime_archive)
    runtime_path = _runtime_archive_path(build)
    files: dict[str, Path | bytes] = {
        "plugin.yaml": project.manifest_path,
        "build.json": json_bytes(build.model_dump(mode="json")),
        runtime_path: runtime_archive,
    }
    for source_file in project.source_files():
        relative = source_file.relative_to(project.source_dir).as_posix()
        files[f"plugin/{relative}"] = source_file
    for name in files:
        validate_package_member_name(name)
    checksums = {name: content_sha256(content) for name, content in files.items()}
    files["checksums.json"] = json_bytes(checksums)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_label = "conda" if build.runtime.type == "conda-pack" else "docker"
    destination = output_dir / (
        f"{build.plugin_id}-{build.plugin_version}-linux-{build.target.arch}-{runtime_label}.pypkg"
    )
    temporary_tar = output_dir / f".{destination.name}.tar.tmp"
    try:
        _write_tar(temporary_tar, files, source_date_epoch)
        compress_zstd(temporary_tar, destination)
    finally:
        temporary_tar.unlink(missing_ok=True)
    return destination


def validate_package_member_name(name: str) -> str:
    """Validate a package tar member name and return its normalized POSIX form."""
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise ValueError("unsafe package member path")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("unsafe package member path")
    normalized = path.as_posix()
    if normalized != name.rstrip("/") or len(normalized) > 1024:
        raise ValueError("unsafe package member path")
    return normalized


def json_bytes(value: object) -> bytes:
    document = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return document.encode("utf-8") + b"\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_sha256(content: Path | bytes) -> str:
    if isinstance(content, bytes):
        return hashlib.sha256(content).hexdigest()
    return sha256_file(content)


def compress_zstd(source: Path, destination: Path) -> None:
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


def _runtime_archive_path(build: PluginBuildManifest) -> str:
    expected = "runtime/env.tar.zst" if build.runtime.type == "conda-pack" else "image.tar.zst"
    if build.runtime.archive != expected:
        raise ValueError("build runtime archive path is not compatible with the Hub")
    return expected


def _verify_build_matches_sources(
    build: PluginBuildManifest,
    project: PluginProject,
    runtime_archive: Path,
) -> None:
    """Reject manifests whose digests disagree with the packaged inputs."""
    actual_source_sha256 = project.source_digest()
    if build.source_sha256 != actual_source_sha256:
        raise ValueError(
            "build.json source_sha256 does not match the plugin sources; "
            "rebuild the manifest with the current project files"
        )
    if (
        isinstance(build.runtime, CondaPackRuntime)
        and build.runtime.fingerprint != sha256_file(runtime_archive)
    ):
        raise ValueError(
            "conda runtime fingerprint does not match the runtime archive; "
            "the prepared runtime belongs to a different build"
        )


def _write_tar(destination: Path, files: dict[str, Path | bytes], epoch: int) -> None:
    directories = _parent_directories(files)
    with tarfile.open(destination, mode="w:", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(directories):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            _normalize_tar_info(info, epoch, 0o755)
            archive.addfile(info)
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(validate_package_member_name(name))
            _normalize_tar_info(info, epoch, 0o644)
            if isinstance(content, bytes):
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            else:
                if content.is_symlink():
                    raise ValueError("package inputs must not be symbolic links")
                info.size = content.stat().st_size
                with content.open("rb") as source:
                    archive.addfile(info, source)


def _parent_directories(files: dict[str, Path | bytes]) -> set[str]:
    directories: set[str] = set()
    for name in files:
        path = PurePosixPath(validate_package_member_name(name))
        parents = list(path.parents)
        for parent in parents[:-1]:
            directories.add(parent.as_posix())
    return directories


def _normalize_tar_info(info: tarfile.TarInfo, epoch: int, mode: int) -> None:
    info.mtime = epoch
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
