"""Bounded verification and UUID-scoped staging for plugin packages."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Final
from uuid import uuid4

import yaml
import zstandard
from pydantic import ValidationError
from python_hub_contracts import PluginBuildManifest, PluginManifest, load_plugin_manifest

from hub_server.errors import HubError
from hub_server.services.signing import PackageSignatureError, verify_package_signature
from hub_server.storage import LocalStorage

_CHUNK_SIZE_BYTES: Final = 1024 * 1024
_MAX_PACKAGE_SIZE_BYTES: Final = 12 * 1024 * 1024 * 1024
_MAX_TAR_SIZE_BYTES: Final = 24 * 1024 * 1024 * 1024
_MAX_MEMBER_SIZE_BYTES: Final = 12 * 1024 * 1024 * 1024
_MAX_MEMBER_COUNT: Final = 100_000
_MAX_METADATA_SIZE_BYTES: Final = 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class VerifiedPluginPackage:
    """Validated package metadata and paths retained in private staging."""

    manifest: PluginManifest
    build: PluginBuildManifest
    source_dir: Path
    archive: Path
    signature_fingerprint: str | None = None
    """Fingerprint of the configured key that verified the package (B9), or
    None when no signature was supplied/verified (keys unconfigured or the
    package shipped unsigned while ``require_signed`` is false)."""


class PluginArchiveService:
    """Verify an untrusted zstd tar before retaining its extracted contents."""

    def __init__(
        self,
        data_root: Path,
        *,
        max_package_size_bytes: int = _MAX_PACKAGE_SIZE_BYTES,
        max_tar_size_bytes: int = _MAX_TAR_SIZE_BYTES,
        max_member_size_bytes: int = _MAX_MEMBER_SIZE_BYTES,
        max_member_count: int = _MAX_MEMBER_COUNT,
    ) -> None:
        limits = (
            max_package_size_bytes,
            max_tar_size_bytes,
            max_member_size_bytes,
            max_member_count,
        )
        if any(limit <= 0 for limit in limits):
            raise ValueError("plugin package limits must be positive")
        self._storage = LocalStorage(data_root)
        self._max_package_size_bytes = max_package_size_bytes
        self._max_tar_size_bytes = max_tar_size_bytes
        self._max_member_size_bytes = max_member_size_bytes
        self._max_member_count = max_member_count

    def verify_and_install(
        self,
        upload: BinaryIO,
        package_sha256: str,
        *,
        signature: bytes | None = None,
        public_keys: Sequence[str] = (),
        require_signed: bool = False,
    ) -> VerifiedPluginPackage:
        """Verify and retain a package only below a private UUID staging directory.

        Signature handling (B9/G9): with ``public_keys`` configured, a supplied
        ``signature`` document must verify against one of them or the package is
        rejected (422) before any decompression work. Without a signature the
        behaviour depends on ``require_signed``: false keeps the pre-B9
        behaviour (unsigned packages are accepted), true rejects the package.
        With no keys configured nothing is verified regardless of ``require_signed``
        (the settings layer refuses require_signed=true with an empty key list).
        """
        staging_id = uuid4().hex
        temporary = self._storage.create_temporary_directory("plugins/.staging")
        try:
            compressed = temporary / "package.pypkg"
            actual_package_sha256 = self._copy_upload(upload, compressed)
            if not _SHA256_PATTERN.fullmatch(package_sha256) or (
                actual_package_sha256 != package_sha256
            ):
                raise self._checksum_error()

            signature_fingerprint = self._verify_signature(
                actual_package_sha256, signature, public_keys, require_signed
            )

            tar_path = temporary / "package.tar"
            self._decompress(compressed, tar_path)
            compressed.unlink()
            with tarfile.open(tar_path, mode="r:") as archive:
                members = archive.getmembers()
                normalized = self._validate_headers(members)
                checksums = self._load_checksums(archive, normalized)
                self._extract_verified(archive, normalized, checksums, temporary)
            tar_path.unlink()

            manifest = load_plugin_manifest(temporary / "plugin.yaml")
            build = PluginBuildManifest.model_validate_json(
                (temporary / "build.json").read_bytes()
            )
            build.assert_matches_plugin(manifest)
            runtime_archive = self._validate_layout(normalized, build)

            staged = self._storage.install_directory(
                temporary, f"plugins/.staging/{staging_id}"
            )
            return VerifiedPluginPackage(
                manifest=manifest,
                build=build,
                source_dir=staged / "plugin",
                archive=staged / PurePosixPath(runtime_archive),
                signature_fingerprint=signature_fingerprint,
            )
        except HubError:
            self._storage.discard_temporary_directory(temporary)
            raise
        except (
            OSError,
            UnicodeError,
            ValueError,
            yaml.YAMLError,  # a malformed plugin.yaml inside an otherwise valid layout
            ValidationError,
            tarfile.TarError,
        ) as error:
            self._storage.discard_temporary_directory(temporary)
            raise self._invalid_error() from error
        except zstandard.ZstdError as error:
            self._storage.discard_temporary_directory(temporary)
            raise self._invalid_error() from error

    def _verify_signature(
        self,
        package_sha256: str,
        signature: bytes | None,
        public_keys: Sequence[str],
        require_signed: bool,
    ) -> str | None:
        """Gate the package on its signature before any decompression work.

        Verification runs on the recomputed digest of the stored upload, right
        after the outer checksum gate: a signature failure is a cheap rejection
        against content that was never extracted.
        """
        if signature is not None and public_keys:
            try:
                return verify_package_signature(package_sha256, signature, public_keys)
            except PackageSignatureError as error:
                raise self._signature_invalid_error() from error
        if require_signed:
            # Either no signature was supplied, or a signature arrived while no
            # key is configured: nothing can count as a valid signature here.
            raise self._signature_required_error()
        return None

    def _copy_upload(self, upload: BinaryIO, destination: Path) -> str:
        digest = hashlib.sha256()
        size = 0
        with destination.open("xb") as package_file:
            while chunk := upload.read(_CHUNK_SIZE_BYTES):
                if not isinstance(chunk, bytes):
                    raise ValueError("plugin package stream must be binary")
                size += len(chunk)
                if size > self._max_package_size_bytes:
                    raise self._too_large_error()
                package_file.write(chunk)
                digest.update(chunk)
        return digest.hexdigest()

    def _decompress(self, source: Path, destination: Path) -> None:
        total = 0
        max_window_size = min(self._max_tar_size_bytes, 2**31 - 1)
        decompressor = zstandard.ZstdDecompressor(max_window_size=max_window_size)
        with (
            source.open("rb") as compressed,
            destination.open("xb") as tar_file,
            decompressor.stream_reader(compressed) as reader,
        ):
            while chunk := reader.read(_CHUNK_SIZE_BYTES):
                total += len(chunk)
                if total > self._max_tar_size_bytes:
                    raise self._too_large_error()
                tar_file.write(chunk)

    def _validate_headers(
        self, members: list[tarfile.TarInfo]
    ) -> dict[str, tarfile.TarInfo]:
        if not members or len(members) > self._max_member_count:
            raise self._too_large_error()
        normalized: dict[str, tarfile.TarInfo] = {}
        total_size = 0
        for member in members:
            name = self._normalize_name(member.name)
            if name in normalized:
                raise self._invalid_error()
            if not (member.isfile() or member.isdir() or member.issym()):
                raise self._invalid_error()
            if member.size < 0 or member.size > self._max_member_size_bytes:
                raise self._too_large_error()
            if member.isfile():
                total_size += member.size
                if total_size > self._max_tar_size_bytes:
                    raise self._too_large_error()
            elif member.size != 0:
                raise self._invalid_error()
            if member.issym():
                self._validate_link_target(name, member.linkname, Path("C:/staging"))
            normalized[name] = member
        return normalized

    def _load_checksums(
        self,
        archive: tarfile.TarFile,
        members: Mapping[str, tarfile.TarInfo],
    ) -> dict[str, str]:
        checksum_member = members.get("checksums.json")
        if checksum_member is None or not checksum_member.isfile():
            raise self._invalid_error()
        raw = self._read_member(archive, checksum_member, _MAX_METADATA_SIZE_BYTES)

        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate checksum key")
                result[key] = value
            return result

        loaded = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(loaded, dict):
            raise self._invalid_error()
        checksums: dict[str, str] = {}
        for raw_name, raw_digest in loaded.items():
            if not isinstance(raw_name, str) or not isinstance(raw_digest, str):
                raise self._invalid_error()
            name = self._normalize_name(raw_name)
            if name != raw_name or not _SHA256_PATTERN.fullmatch(raw_digest):
                raise self._invalid_error()
            checksums[name] = raw_digest

        regular_files = {name for name, member in members.items() if member.isfile()}
        if set(checksums) != regular_files - {"checksums.json"}:
            raise self._checksum_error()
        return checksums

    def _extract_verified(
        self,
        archive: tarfile.TarFile,
        members: Mapping[str, tarfile.TarInfo],
        checksums: Mapping[str, str],
        root: Path,
    ) -> None:
        for name, member in sorted(members.items(), key=lambda item: item[0].count("/")):
            destination = root / PurePosixPath(name)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
        for name, member in members.items():
            if not member.isfile():
                continue
            destination = root / PurePosixPath(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            total = 0
            extracted = archive.extractfile(member)
            if extracted is None:
                raise self._invalid_error()
            with extracted, destination.open("xb") as output:
                while chunk := extracted.read(_CHUNK_SIZE_BYTES):
                    total += len(chunk)
                    output.write(chunk)
                    digest.update(chunk)
            if total != member.size:
                raise self._invalid_error()
            if name != "checksums.json" and digest.hexdigest() != checksums[name]:
                raise self._checksum_error()
        for name, member in members.items():
            if not member.issym():
                continue
            destination = root / PurePosixPath(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._validate_link_target(name, member.linkname, root)
            os.symlink(member.linkname, destination)

    def _validate_layout(
        self,
        members: Mapping[str, tarfile.TarInfo],
        build: PluginBuildManifest,
    ) -> str:
        runtime_archive = (
            "runtime/env.tar.zst" if build.runtime.type == "conda-pack" else "image.tar.zst"
        )
        if build.runtime.archive != runtime_archive:
            raise self._invalid_error()
        required_files = {"plugin.yaml", "build.json", "checksums.json", runtime_archive}
        regular_files = {name for name, member in members.items() if member.isfile()}
        if not required_files.issubset(regular_files):
            raise self._invalid_error()
        if "plugin" not in members or not members["plugin"].isdir():
            raise self._invalid_error()
        if not any(name.startswith("plugin/") for name in regular_files):
            raise self._invalid_error()

        for name in members:
            allowed = (
                name in required_files
                or name == "plugin"
                or name.startswith("plugin/")
                or (runtime_archive.startswith("runtime/") and name == "runtime")
            )
            if not allowed:
                raise self._invalid_error()
        return runtime_archive

    @staticmethod
    def _read_member(
        archive: tarfile.TarFile, member: tarfile.TarInfo, limit: int
    ) -> bytes:
        if member.size > limit:
            raise PluginArchiveService._too_large_error()
        extracted = archive.extractfile(member)
        if extracted is None:
            raise PluginArchiveService._invalid_error()
        with extracted:
            content = extracted.read(limit + 1)
        if len(content) != member.size or len(content) > limit:
            raise PluginArchiveService._too_large_error()
        return content

    @staticmethod
    def _normalize_name(name: str) -> str:
        if (
            not name
            or "\\" in name
            or "\x00" in name
            or name.startswith("/")
            or re.match(r"^[A-Za-z]:", name)
        ):
            raise PluginArchiveService._invalid_error()
        path = PurePosixPath(name)
        if any(part in {"", ".", ".."} for part in path.parts):
            raise PluginArchiveService._invalid_error()
        normalized = path.as_posix()
        if normalized != name.rstrip("/") or len(normalized) > 1024:
            raise PluginArchiveService._invalid_error()
        return normalized

    @staticmethod
    def _validate_link_target(name: str, target: str, root: Path) -> None:
        if (
            not target
            or "\\" in target
            or "\x00" in target
            or target.startswith("/")
            or re.match(r"^[A-Za-z]:", target)
        ):
            raise PluginArchiveService._invalid_error()
        destination = root / PurePosixPath(name)
        resolved = (destination.parent / PurePosixPath(target)).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as error:
            raise PluginArchiveService._invalid_error() from error

    @staticmethod
    def _checksum_error() -> HubError:
        return HubError(
            code="PLUGIN_PACKAGE_CHECKSUM_MISMATCH",
            message="PLUGIN_PACKAGE_CHECKSUM_MISMATCH: 插件包校验和不匹配",
            status_code=422,
        )

    @staticmethod
    def _signature_invalid_error() -> HubError:
        return HubError(
            code="PLUGIN_PACKAGE_SIGNATURE_INVALID",
            message="插件包签名验证失败: 签名格式非法或不匹配任何已配置公钥",
            status_code=422,
        )

    @staticmethod
    def _signature_required_error() -> HubError:
        return HubError(
            code="PLUGIN_PACKAGE_SIGNATURE_REQUIRED",
            message="当前部署要求插件包附带有效签名 (plugins.signature.require_signed)",
            status_code=422,
        )

    @staticmethod
    def _invalid_error() -> HubError:
        return HubError(
            code="PLUGIN_PACKAGE_INVALID",
            message="插件包格式无效",
            status_code=422,
        )

    @staticmethod
    def _too_large_error() -> HubError:
        return HubError(
            code="PLUGIN_PACKAGE_TOO_LARGE",
            message="插件包超过安全大小限制",
            status_code=413,
        )
