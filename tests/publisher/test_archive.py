from __future__ import annotations

import hashlib
import io
import json
import random
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import zstandard
from hub_publisher.archive import (
    DEFAULT_ZSTD_LEVEL,
    compress_zstd,
    create_plugin_package,
    validate_package_member_name,
    zstd_level,
)
from hub_publisher.project import PluginProject
from python_hub_contracts import PluginBuildManifest

from tests.publisher.test_project import _write_project


def _sample_source_sha256() -> str:
    # The digest basis is the entrypoint package directory (src/sample_plugin),
    # matching the legacy NC script basis so rebuilds keep their metadata.
    return hashlib.sha256(b"__init__.py\0\0").hexdigest()


def _build_manifest(
    runtime_type: str,
    runtime_sha256: str,
    *,
    source_sha256: str | None = None,
) -> PluginBuildManifest:
    runtime = (
        {
            "type": "conda-pack",
            "archive": "runtime/env.tar.zst",
            "fingerprint": runtime_sha256,
        }
        if runtime_type == "conda-pack"
        else {
            "type": "docker",
            "archive": "image.tar.zst",
            "image": "sample_plugin:1.2.3-linux-amd64",
            "digest": runtime_sha256,
        }
    )
    return PluginBuildManifest.model_validate(
        {
            "schema_version": "1.0",
            "build_id": f"sample_plugin-1.2.3-linux-amd64-{runtime_type}-abc123",
            "plugin_id": "sample_plugin",
            "plugin_version": "1.2.3",
            "target": {"os": "linux", "arch": "amd64"},
            "python_version": "3.12",
            "runtime": runtime,
            "sdk_version": "1.0.0",
            "source_sha256": source_sha256 or _sample_source_sha256(),
            "built_at": datetime.fromtimestamp(0, tz=UTC),
        }
    )


def _members(package: Path) -> dict[str, bytes]:
    with (
        package.open("rb") as compressed,
        zstandard.ZstdDecompressor().stream_reader(compressed) as reader,
        tarfile.open(fileobj=reader, mode="r|") as archive,
    ):
        result: dict[str, bytes] = {}
        for member in archive:
            if member.isfile():
                extracted = archive.extractfile(member)
                assert extracted is not None
                result[member.name] = extracted.read()
        return result


def test_create_plugin_package_is_byte_reproducible_at_fixed_epoch(
    tmp_path: Path,
) -> None:
    """Host timestamps or unsorted members would make identical plugin packages differ."""
    _write_project(tmp_path / "project")
    project = PluginProject.load(tmp_path / "project")
    runtime = tmp_path / "env.tar.zst"
    runtime.write_bytes(b"prepared runtime")
    runtime_sha256 = hashlib.sha256(runtime.read_bytes()).hexdigest()
    build = _build_manifest("conda-pack", runtime_sha256)

    first = create_plugin_package(project, build, runtime, tmp_path / "first", 0)
    second = create_plugin_package(project, build, runtime, tmp_path / "second", 0)

    assert first.read_bytes() == second.read_bytes()
    members = _members(first)
    assert set(members) == {
        "plugin.yaml",
        "build.json",
        "checksums.json",
        "runtime/env.tar.zst",
        "plugin/sample_plugin/__init__.py",
    }
    assert members["plugin.yaml"] == (tmp_path / "project" / "plugin.yaml").read_bytes()
    assert PluginBuildManifest.model_validate_json(members["build.json"]) == build
    checksums = json.loads(members["checksums.json"])
    assert checksums == {
        name: hashlib.sha256(content).hexdigest()
        for name, content in members.items()
        if name != "checksums.json"
    }


@pytest.mark.parametrize("name", ["/plugin.py", "plugin/../evil.py", "C:/evil.py"])
def test_package_member_names_reject_escape_paths(name: str) -> None:
    """Accepting absolute or traversal names would create Zip-Slip style packages."""
    with pytest.raises(ValueError):
        validate_package_member_name(name)


def test_create_plugin_package_rejects_runtime_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Packaging a runtime symlink would make the immutable archive depend on host state."""
    _write_project(tmp_path / "project")
    project = PluginProject.load(tmp_path / "project")
    runtime = tmp_path / "real.tar.zst"
    runtime.write_bytes(b"prepared runtime")
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self == runtime or original_is_symlink(self),
    )
    build = _build_manifest("conda-pack", hashlib.sha256(runtime.read_bytes()).hexdigest())

    with pytest.raises(ValueError, match="symbolic link"):
        create_plugin_package(project, build, runtime, tmp_path / "out", 0)


def test_create_plugin_package_rejects_tampered_source_digest(tmp_path: Path) -> None:
    """Trusting build.json source metadata would make packages unreproducible."""
    _write_project(tmp_path / "project")
    project = PluginProject.load(tmp_path / "project")
    runtime = tmp_path / "env.tar.zst"
    runtime.write_bytes(b"prepared runtime")
    build = _build_manifest(
        "conda-pack",
        hashlib.sha256(runtime.read_bytes()).hexdigest(),
        source_sha256="f" * 64,
    )

    with pytest.raises(ValueError, match="source_sha256"):
        create_plugin_package(project, build, runtime, tmp_path / "out", 0)


def test_create_plugin_package_rejects_tampered_conda_fingerprint(tmp_path: Path) -> None:
    """A conda fingerprint that does not match the archive would install the wrong runtime."""
    _write_project(tmp_path / "project")
    project = PluginProject.load(tmp_path / "project")
    runtime = tmp_path / "env.tar.zst"
    runtime.write_bytes(b"prepared runtime")
    build = _build_manifest("conda-pack", "f" * 64)

    with pytest.raises(ValueError, match="fingerprint"):
        create_plugin_package(project, build, runtime, tmp_path / "out", 0)


def test_zstd_level_defaults_to_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    """Level 19 costs ~7x the CPU of level 10 for ~12% less output."""
    monkeypatch.delenv("HUB_PLUGIN_ZSTD_LEVEL", raising=False)

    assert zstd_level() == 10


@pytest.mark.parametrize("raw", ["", "not-a-number", "0", "23", "  "])
def test_zstd_level_falls_back_on_unusable_override(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """A mistyped environment variable must not break a build."""
    monkeypatch.setenv("HUB_PLUGIN_ZSTD_LEVEL", raw)

    assert zstd_level() == DEFAULT_ZSTD_LEVEL


def test_zstd_level_honours_in_range_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_PLUGIN_ZSTD_LEVEL", "19")

    assert zstd_level() == 19


def _mixed_payload() -> bytes:
    """A ~1MB slice that behaves like an image tar: repeated paths plus random bytes."""
    rng = random.Random(20260925)
    block = b"service-hub plugin layer payload "
    return b"".join(
        block * 40 + bytes(rng.randrange(256) for _ in range(512)) for _ in range(600)
    )


def test_compress_zstd_roundtrip_uses_default_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HUB_PLUGIN_ZSTD_LEVEL", raising=False)
    source = tmp_path / "payload.bin"
    source.write_bytes(_mixed_payload())
    destination = tmp_path / "payload.bin.zst"
    slower = tmp_path / "slower.zst"

    compress_zstd(source, destination)
    compress_zstd(source, slower, level=3)

    assert zstandard.ZstdDecompressor().stream_reader(
        io.BytesIO(destination.read_bytes())
    ).read() == source.read_bytes()
    # Level 10 must actually be in use: it beats level 3 on size.
    assert destination.stat().st_size < slower.stat().st_size


def test_compress_zstd_explicit_level_overrides_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Benchmarks and tests must be able to pin a level regardless of the environment."""
    monkeypatch.setenv("HUB_PLUGIN_ZSTD_LEVEL", "3")
    source = tmp_path / "payload.bin"
    source.write_bytes(_mixed_payload())

    from_environment = tmp_path / "from-env.zst"
    pinned = tmp_path / "pinned.zst"
    compress_zstd(source, from_environment)
    compress_zstd(source, pinned, level=19)

    assert pinned.stat().st_size < from_environment.stat().st_size
