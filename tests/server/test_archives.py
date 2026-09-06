"""Security boundary tests for uploaded dual-runtime plugin packages."""

import hashlib
import io
import json
import tarfile
from collections.abc import Mapping
from pathlib import Path

import pytest
import zstandard
from hub_server.errors import HubError
from hub_server.services.archives import PluginArchiveService


def _plugin_manifest() -> bytes:
    fixture = Path(__file__).parents[1] / "fixtures" / "valid-plugin.yaml"
    return fixture.read_bytes()


def _build_manifest(runtime_type: str = "conda-pack") -> bytes:
    fixture = Path(__file__).parents[1] / "fixtures" / "valid-build.json"
    build = json.loads(fixture.read_text("utf-8"))
    build["target"]["arch"] = "amd64"
    if runtime_type == "docker":
        build["runtime"] = {
            "type": "docker",
            "archive": "image.tar.zst",
            "image": "nc-to-shp:1.0.0",
            "digest": "1" * 64,
        }
    return json.dumps(build, separators=(",", ":")).encode()


def _make_pypkg(
    *,
    runtime_type: str = "conda-pack",
    extra_members: Mapping[str, bytes] | None = None,
    unlisted: frozenset[str] = frozenset(),
    checksum_overrides: Mapping[str, str] | None = None,
    duplicate: str | None = None,
    symlinks: Mapping[str, str] | None = None,
) -> tuple[io.BytesIO, str]:
    runtime_path = "runtime/env.tar.zst" if runtime_type == "conda-pack" else "image.tar.zst"
    members = {
        "plugin.yaml": _plugin_manifest(),
        "build.json": _build_manifest(runtime_type),
        "plugin/main.py": b"def run():\n    return None\n",
        runtime_path: b"runtime archive",
        **(extra_members or {}),
    }
    checksums = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in members.items()
        if name not in unlisted
    }
    checksums.update(checksum_overrides or {})
    members["checksums.json"] = json.dumps(checksums, sort_keys=True).encode()

    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:") as archive:
        directories = {"plugin"}
        if runtime_type == "conda-pack":
            directories.add("runtime")
        for directory in sorted(directories):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
            if duplicate == name:
                archive.addfile(info, io.BytesIO(content))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            archive.addfile(info)

    package = zstandard.ZstdCompressor().compress(tar_bytes.getvalue())
    return io.BytesIO(package), hashlib.sha256(package).hexdigest()


def test_valid_package_is_verified_into_uuid_staging_only(tmp_path: Path) -> None:
    """Installing under a manifest/public Build ID before persistence leaves orphan Builds."""
    upload, package_sha256 = _make_pypkg()

    verified = PluginArchiveService(tmp_path / "data").verify_and_install(
        upload, package_sha256
    )

    staging = verified.source_dir.parent
    assert verified.manifest.plugin.id == "nc_to_shp"
    assert verified.build.runtime.type == "conda-pack"
    assert staging.parent == tmp_path / "data" / "plugins" / ".staging"
    assert len(staging.name) == 32
    assert verified.source_dir == staging / "plugin"
    assert verified.archive == staging / "runtime" / "env.tar.zst"
    assert verified.archive.read_bytes() == b"runtime archive"
    assert {path.name for path in (tmp_path / "data" / "plugins").iterdir()} == {".staging"}


def test_valid_docker_package_selects_top_level_image_archive(tmp_path: Path) -> None:
    """Treating a Docker package as Conda hands the worker the wrong runtime artifact."""
    upload, package_sha256 = _make_pypkg(runtime_type="docker")

    verified = PluginArchiveService(tmp_path / "data").verify_and_install(
        upload, package_sha256
    )

    assert verified.build.runtime.type == "docker"
    assert verified.archive == verified.source_dir.parent / "image.tar.zst"
    assert verified.archive.read_bytes() == b"runtime archive"


def test_package_rejects_member_not_listed_in_checksums(tmp_path: Path) -> None:
    """An unlisted regular member bypasses the package integrity allowlist."""
    upload, package_sha256 = _make_pypkg(
        extra_members={"plugin/extra.py": b"x"},
        unlisted=frozenset({"plugin/extra.py"}),
    )

    with pytest.raises(HubError) as raised:
        PluginArchiveService(tmp_path / "data").verify_and_install(upload, package_sha256)

    assert raised.value.code == "PLUGIN_PACKAGE_CHECKSUM_MISMATCH"
    assert not list((tmp_path / "data" / "plugins" / ".staging").iterdir())


def test_package_rejects_member_content_that_does_not_match_checksum(tmp_path: Path) -> None:
    """Merely listing a member cannot substitute for verifying its actual bytes."""
    upload, package_sha256 = _make_pypkg(checksum_overrides={"plugin/main.py": "0" * 64})

    with pytest.raises(HubError) as raised:
        PluginArchiveService(tmp_path / "data").verify_and_install(upload, package_sha256)

    assert raised.value.code == "PLUGIN_PACKAGE_CHECKSUM_MISMATCH"


@pytest.mark.parametrize(
    ("extra_members", "symlinks"),
    [
        ({"../escape.py": b"x"}, None),
        ({"C:/escape.py": b"x"}, None),
        ({"plugin\\escape.py": b"x"}, None),
        ({}, {"plugin/link.py": "../../escape.py"}),
    ],
)
def test_package_rejects_unsafe_member_paths_and_link_targets(
    tmp_path: Path,
    extra_members: Mapping[str, bytes],
    symlinks: Mapping[str, str] | None,
) -> None:
    """Traversal, non-POSIX names, or escaping links could write outside staging."""
    upload, package_sha256 = _make_pypkg(extra_members=extra_members, symlinks=symlinks)

    with pytest.raises(HubError) as raised:
        PluginArchiveService(tmp_path / "data").verify_and_install(upload, package_sha256)

    assert raised.value.code == "PLUGIN_PACKAGE_INVALID"
    assert not list((tmp_path / "data" / "plugins" / ".staging").iterdir())


def test_package_rejects_duplicate_members(tmp_path: Path) -> None:
    """Duplicate tar names make checksum verification dependent on extraction order."""
    upload, package_sha256 = _make_pypkg(duplicate="plugin/main.py")

    with pytest.raises(HubError) as raised:
        PluginArchiveService(tmp_path / "data").verify_and_install(upload, package_sha256)

    assert raised.value.code == "PLUGIN_PACKAGE_INVALID"


def test_package_rejects_wrong_outer_sha256_without_retaining_staging(tmp_path: Path) -> None:
    """The checksum manifest cannot protect itself when the outer package hash is wrong."""
    upload, _ = _make_pypkg()

    with pytest.raises(HubError) as raised:
        PluginArchiveService(tmp_path / "data").verify_and_install(upload, "0" * 64)

    assert raised.value.code == "PLUGIN_PACKAGE_CHECKSUM_MISMATCH"
    assert not list((tmp_path / "data" / "plugins" / ".staging").iterdir())


def test_package_rejects_runtime_archive_at_noncanonical_path(tmp_path: Path) -> None:
    """Accepting a Docker archive outside its V1 path breaks runner/package portability."""
    build = json.loads(_build_manifest("docker"))
    build["runtime"]["archive"] = "runtime/image.tar.zst"
    upload, package_sha256 = _make_pypkg(
        runtime_type="docker",
        extra_members={"build.json": json.dumps(build).encode()},
    )

    with pytest.raises(HubError) as raised:
        PluginArchiveService(tmp_path / "data").verify_and_install(upload, package_sha256)

    assert raised.value.code == "PLUGIN_PACKAGE_INVALID"


def test_package_checks_declared_member_size_before_extraction(tmp_path: Path) -> None:
    """Trusting a large declared member can exhaust storage during extraction."""
    upload, package_sha256 = _make_pypkg()

    with pytest.raises(HubError) as raised:
        PluginArchiveService(
            tmp_path / "data", max_member_size_bytes=8
        ).verify_and_install(upload, package_sha256)

    assert raised.value.code == "PLUGIN_PACKAGE_TOO_LARGE"


def test_package_checks_member_count_before_extraction(tmp_path: Path) -> None:
    """An archive with too many tiny members can exhaust metadata and inode limits."""
    upload, package_sha256 = _make_pypkg()

    with pytest.raises(HubError) as raised:
        PluginArchiveService(tmp_path / "data", max_member_count=5).verify_and_install(
            upload, package_sha256
        )

    assert raised.value.code == "PLUGIN_PACKAGE_TOO_LARGE"
