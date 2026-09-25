"""Offline verification of built `.pypkg` plugin packages.

Self-contained twin of the Hub-side accept rules in
``hub_server.services.archives.PluginArchiveService`` (layout whitelist,
``checksums.json`` coverage, per-member digests, member name and link-target
safety, size limits). hub_publisher must not import hub_server, so the shared
rules are re-implemented here with identical limits on purpose: when a rule
changes on either side, change both and keep this cross-reference in sync.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tarfile
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Final

import zstandard
from pydantic import ValidationError
from python_hub_contracts import PluginBuildManifest, PluginManifest, load_plugin_manifest

CHUNK_SIZE_BYTES: Final = 1024 * 1024
# Limits mirror hub_server.services.archives one-for-one (see module docstring).
MAX_TAR_SIZE_BYTES: Final = 24 * 1024 * 1024 * 1024
MAX_MEMBER_SIZE_BYTES: Final = 12 * 1024 * 1024 * 1024
MAX_MEMBER_COUNT: Final = 100_000
MAX_METADATA_SIZE_BYTES: Final = 1024 * 1024
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


def verify_plugin_package(package: Path) -> list[str]:
    """Return one human-readable problem per rule violation, in check order."""
    if not package.is_file():
        return [f"package is not a file: {package}"]
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="hub-plugin-validate-") as temporary_name:
        temporary = Path(temporary_name)
        tar_path = temporary / "package.tar"
        decompress_problem = _decompress(package, tar_path)
        if decompress_problem is not None:
            return [decompress_problem]
        try:
            with tarfile.open(tar_path, mode="r:") as archive:
                members = _collect_members(archive.getmembers(), problems)
                checksums = _load_checksums(archive, members, problems)
                build = _load_build_manifest(archive, members, problems)
                manifest = _load_source_manifest(archive, members, temporary, problems)
                _check_manifest_agreement(build, manifest, problems)
                _validate_layout(members, build, problems)
                _verify_member_digests(archive, members, checksums, problems)
        except tarfile.TarError as error:
            problems.append(f"package is not a readable tar archive: {_one_line(str(error))}")
    return problems


def _decompress(source: Path, destination: Path) -> str | None:
    total = 0
    max_window_size = min(MAX_TAR_SIZE_BYTES, 2**31 - 1)
    decompressor = zstandard.ZstdDecompressor(max_window_size=max_window_size)
    try:
        with (
            source.open("rb") as compressed,
            destination.open("xb") as tar_file,
            decompressor.stream_reader(compressed) as reader,
        ):
            while chunk := reader.read(CHUNK_SIZE_BYTES):
                total += len(chunk)
                if total > MAX_TAR_SIZE_BYTES:
                    return "package tar exceeds the size limit"
                tar_file.write(chunk)
    except OSError as error:
        return f"package cannot be read: {_one_line(str(error))}"
    except zstandard.ZstdError as error:
        return f"package is not a valid zstd stream: {_one_line(str(error))}"
    return None


def _collect_members(
    raw_members: list[tarfile.TarInfo], problems: list[str]
) -> dict[str, tarfile.TarInfo]:
    if not raw_members:
        problems.append("package archive is empty")
    members: dict[str, tarfile.TarInfo] = {}
    total_size = 0
    for member in raw_members:
        try:
            name = _normalize_name(member.name)
        except ValueError:
            problems.append(f"package member has an unsafe path: {member.name!r}")
            continue
        if name in members:
            problems.append(f"package contains duplicate member names: {name}")
            continue
        if not (member.isfile() or member.isdir() or member.issym()):
            problems.append(
                f"package member is not a regular file, directory, or symbolic link: {name}"
            )
            continue
        if member.size < 0 or member.size > MAX_MEMBER_SIZE_BYTES:
            problems.append(f"package member exceeds the size limit: {name}")
            continue
        if member.isfile():
            total_size += member.size
            if total_size > MAX_TAR_SIZE_BYTES:
                problems.append(f"package members exceed the total size limit: {name}")
                continue
        elif member.size != 0:
            problems.append(f"package non-file member has a non-zero size: {name}")
            continue
        if member.issym():
            _check_link_target(name, member.linkname, problems)
        members[name] = member
    return members


def _load_checksums(
    archive: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    problems: list[str],
) -> dict[str, str]:
    checksum_member = members.get("checksums.json")
    if checksum_member is None or not checksum_member.isfile():
        problems.append("package is missing the checksums.json member")
        return {}
    raw = _read_member(archive, checksum_member, problems)
    if raw is None:
        return {}

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate checksum key")
            result[key] = value
        return result

    try:
        loaded = json.loads(raw, object_pairs_hook=unique_object)
    except ValueError as error:
        problems.append(f"checksums.json is not usable: {_one_line(str(error))}")
        return {}
    if not isinstance(loaded, dict):
        problems.append("checksums.json root must be an object")
        return {}

    checksums: dict[str, str] = {}
    for raw_name, raw_digest in loaded.items():
        if not isinstance(raw_name, str) or not isinstance(raw_digest, str):
            problems.append(f"checksums.json entry is malformed: {raw_name!r}")
            continue
        try:
            name = _normalize_name(raw_name)
        except ValueError:
            problems.append(f"checksums.json entry has an unsafe path: {raw_name!r}")
            continue
        if name != raw_name or not _SHA256_PATTERN.fullmatch(raw_digest):
            problems.append(f"checksums.json entry is malformed: {raw_name!r}")
            continue
        checksums[name] = raw_digest

    regular_files = {name for name, member in members.items() if member.isfile()}
    expected = regular_files - {"checksums.json"}
    for name in sorted(expected - set(checksums)):
        problems.append(f"package member is not listed in checksums.json: {name}")
    for name in sorted(set(checksums) - expected):
        problems.append(f"checksums.json lists a member that does not exist: {name}")
    return checksums


def _load_build_manifest(
    archive: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    problems: list[str],
) -> PluginBuildManifest | None:
    member = members.get("build.json")
    if member is None or not member.isfile():
        problems.append("package is missing the build.json member")
        return None
    raw = _read_member(archive, member, problems)
    if raw is None:
        return None
    try:
        return PluginBuildManifest.model_validate_json(raw)
    except ValidationError as error:
        problems.append(
            "build.json is not a valid plugin build manifest: "
            + _one_line(str(error))
        )
        return None


def _load_source_manifest(
    archive: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    temporary: Path,
    problems: list[str],
) -> PluginManifest | None:
    member = members.get("plugin.yaml")
    if member is None or not member.isfile():
        return None  # the layout check reports the missing member
    raw = _read_member(archive, member, problems)
    if raw is None:
        return None
    extracted = temporary / "plugin.yaml"
    extracted.write_bytes(raw)
    try:
        return load_plugin_manifest(extracted)
    except Exception as error:  # manifest parsing raises a wide error variety
        problems.append(
            f"plugin.yaml is not a valid plugin manifest: {_one_line(str(error))}"
        )
        return None


def _check_manifest_agreement(
    build: PluginBuildManifest | None, manifest: PluginManifest | None, problems: list[str]
) -> None:
    """Reject packages whose build.json and plugin.yaml disagree on identity.

    Same rule as the Hub's ``PluginBuildManifest.assert_matches_plugin`` call
    (see module docstring).
    """
    if build is None or manifest is None:
        return
    try:
        build.assert_matches_plugin(manifest)
    except ValueError as error:
        problems.append(f"build.json does not match plugin.yaml: {_one_line(str(error))}")


def _validate_layout(
    members: Mapping[str, tarfile.TarInfo],
    build: PluginBuildManifest | None,
    problems: list[str],
) -> None:
    if build is None:
        return
    runtime_archive = (
        "runtime/env.tar.zst" if build.runtime.type == "conda-pack" else "image.tar.zst"
    )
    if build.runtime.archive != runtime_archive:
        problems.append(
            f"build.json runtime archive must be {runtime_archive}: got {build.runtime.archive}"
        )
    required_files = {"plugin.yaml", "build.json", "checksums.json", runtime_archive}
    regular_files = {name for name, member in members.items() if member.isfile()}
    for name in sorted(required_files - regular_files):
        problems.append(f"package is missing the required member: {name}")
    plugin_directory = members.get("plugin")
    if plugin_directory is None or not plugin_directory.isdir():
        problems.append("package is missing the plugin/ source directory")
    if not any(name.startswith("plugin/") for name in regular_files):
        problems.append("package has no files below plugin/")
    for name in sorted(members):
        allowed = (
            name in required_files
            or name == "plugin"
            or name.startswith("plugin/")
            or (runtime_archive.startswith("runtime/") and name == "runtime")
        )
        if not allowed:
            problems.append(f"package member is outside the layout whitelist: {name}")


def _verify_member_digests(
    archive: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    checksums: Mapping[str, str],
    problems: list[str],
) -> None:
    for name, member in sorted(members.items()):
        if not member.isfile() or name == "checksums.json":
            continue
        if name not in checksums:
            continue  # coverage problems are reported by _load_checksums
        extracted = archive.extractfile(member)
        if extracted is None:
            problems.append(f"package member is unreadable: {name}")
            continue
        digest = hashlib.sha256()
        total = 0
        with extracted:
            while chunk := extracted.read(CHUNK_SIZE_BYTES):
                total += len(chunk)
                digest.update(chunk)
        if total != member.size:
            problems.append(f"package member size does not match its tar header: {name}")
            continue
        if digest.hexdigest() != checksums[name]:
            problems.append(f"package member content does not match checksums.json: {name}")


def _read_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, problems: list[str]
) -> bytes | None:
    if member.size > MAX_METADATA_SIZE_BYTES:
        problems.append(f"package metadata member exceeds the size limit: {member.name}")
        return None
    extracted = archive.extractfile(member)
    if extracted is None:
        problems.append(f"package metadata member is unreadable: {member.name}")
        return None
    with extracted:
        content = extracted.read(MAX_METADATA_SIZE_BYTES + 1)
    if len(content) != member.size or len(content) > MAX_METADATA_SIZE_BYTES:
        problems.append(f"package metadata member is truncated: {member.name}")
        return None
    return content


def _normalize_name(name: str) -> str:
    """Validate a package tar member name and return its normalized POSIX form.

    Same rules as ``validate_package_member_name`` in hub_publisher.archive and
    ``_normalize_name`` in hub_server.services.archives (see module docstring).
    """
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise ValueError(name)
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(name)
    normalized = path.as_posix()
    if normalized != name.rstrip("/") or len(normalized) > 1024:
        raise ValueError(name)
    return normalized


def _check_link_target(name: str, target: str, problems: list[str]) -> None:
    """Reject link targets that are non-POSIX or escape the package root.

    Same rule as ``_validate_link_target`` in hub_server.services.archives (see
    module docstring), resolved against a virtual root because the CLI never
    extracts symbolic links.
    """
    if (
        not target
        or "\\" in target
        or "\x00" in target
        or target.startswith("/")
        or re.match(r"^[A-Za-z]:", target)
    ):
        problems.append(f"package symbolic link has an unsafe target: {name} -> {target!r}")
        return
    resolved = posixpath.normpath(posixpath.join(PurePosixPath(name).parent.as_posix(), target))
    if resolved.startswith("/") or resolved == ".." or resolved.startswith("../"):
        problems.append(f"package symbolic link escapes the package root: {name} -> {target!r}")


def _one_line(text: str) -> str:
    """Collapse an exception message onto one line for the CLI problem report."""
    return re.sub(r"\s+", " ", text).strip()
