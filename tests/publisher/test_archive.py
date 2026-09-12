from __future__ import annotations

import hashlib
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import zstandard
from hub_publisher.archive import create_plugin_package, validate_package_member_name
from hub_publisher.project import PluginProject
from python_hub_contracts import PluginBuildManifest

from tests.publisher.test_project import _write_project


def _build_manifest(runtime_type: str, runtime_sha256: str) -> PluginBuildManifest:
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
            "source_sha256": "0" * 64,
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
