"""Tests for the `hub-plugin validate` subcommand and its offline package checks."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections.abc import Mapping
from pathlib import Path

import pytest
import zstandard
from hub_publisher.archive import create_plugin_package
from hub_publisher.builder import create_build_manifest
from hub_publisher.cli import main
from hub_publisher.project import PluginProject

NC_MANIFEST = """\
spec_version: "1.0"
plugin:
  id: nc_to_shp
  name: NC to Shapefile
  version: "1.0.0"
sdk:
  version: "1.0"
runtime:
  type: process
  python:
    version: "3.12"
entrypoint:
  module: nc_to_shp_plugin.main
  function: run
parameters: []
inputs: []
outputs: []
execution:
  timeout: 60
  concurrency: 1
environment_variables:
  required: []
healthcheck:
  enabled: true
  type: import
"""


def _write_nc_project(root: Path) -> None:
    """Write a minimal nc-to-shp style project (no runtime files)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(NC_MANIFEST, encoding="utf-8")
    package = root / "src" / "nc_to_shp_plugin"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "main.py").write_text("def run():\n    return None\n", encoding="utf-8")


def _make_package(project_root: Path, workdir: Path) -> Path:
    """Build one real conda-pack `.pypkg` through the publisher components."""
    project = PluginProject.load(project_root)
    runtime = workdir / "env.tar.zst"
    runtime.write_bytes(b"prepared runtime")
    build = create_build_manifest(
        project,
        "conda-pack",
        "amd64",
        runtime,
        docker_digest=None,
        source_date_epoch=0,
    )
    return create_plugin_package(project, build, runtime, workdir / "dist", 0)


def _craft_package(
    path: Path,
    *,
    extra_members: Mapping[str, bytes] | None = None,
    checksum_overrides: Mapping[str, str] | None = None,
) -> None:
    """Craft a `.pypkg` with full checksum coverage but arbitrary members.

    Mirrors the package-crafting helper in tests/server/test_archives.py and the
    same fixtures, so both rule sets are exercised against identical inputs.
    """
    fixtures = Path(__file__).parents[1] / "fixtures"
    members: dict[str, bytes] = {
        "plugin.yaml": (fixtures / "valid-plugin.yaml").read_bytes(),
        "build.json": (fixtures / "valid-build.json").read_bytes(),
        "plugin/main.py": b"def run():\n    return None\n",
        "runtime/env.tar.zst": b"runtime archive",
        **(extra_members or {}),
    }
    checksums = {
        name: hashlib.sha256(content).hexdigest() for name, content in members.items()
    }
    checksums.update(checksum_overrides or {})
    members["checksums.json"] = json.dumps(checksums, sort_keys=True).encode()

    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:") as archive:
        for directory in ("plugin", "runtime"):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    path.write_bytes(zstandard.ZstdCompressor().compress(tar_bytes.getvalue()))


def test_validate_accepts_a_minimal_nc_style_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_nc_project(tmp_path / "nc-plugin")

    exit_code = main(["validate", str(tmp_path / "nc-plugin")])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "plugin: nc_to_shp version 1.0.0" in output
    assert "project: OK" in output
    assert "manifest: OK" in output


def test_validate_reports_a_missing_runtime_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_nc_project(tmp_path / "nc-plugin")

    exit_code = main(
        ["validate", str(tmp_path / "nc-plugin"), "--runtime", "conda-pack"]
    )

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "validate: runtime conda-pack: " in stderr
    assert "environment.yml" in stderr

    (tmp_path / "nc-plugin" / "environment.yml").write_text(
        "name: nc\n", encoding="utf-8"
    )
    assert (
        main(["validate", str(tmp_path / "nc-plugin"), "--runtime", "conda-pack"]) == 0
    )
    assert "runtime conda-pack: OK" in capsys.readouterr().out


def test_validate_rejects_a_project_without_src(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "nc-plugin"
    root.mkdir()
    (root / "plugin.yaml").write_text(NC_MANIFEST, encoding="utf-8")

    exit_code = main(["validate", str(root)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert stderr.startswith("validate: ")
    assert "src" in stderr


def test_validate_rejects_symlinked_source_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Following source symlinks could smuggle host files into a plugin package."""
    root = tmp_path / "nc-plugin"
    _write_nc_project(root)
    target = tmp_path / "outside.txt"
    target.write_text("secret", encoding="utf-8")
    linked = root / "src" / "nc_to_shp_plugin" / "outside.txt"
    linked.write_text("placeholder", encoding="utf-8")
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self == linked or original_is_symlink(self),
    )

    exit_code = main(["validate", str(root)])

    assert exit_code == 1
    assert "symbolic link" in capsys.readouterr().err


def test_validate_accepts_a_built_package_with_runtime(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_nc_project(tmp_path / "nc-plugin")
    (tmp_path / "nc-plugin" / "environment.yml").write_text("name: nc\n", encoding="utf-8")
    package = _make_package(tmp_path / "nc-plugin", tmp_path)

    exit_code = main(
        [
            "validate",
            str(tmp_path / "nc-plugin"),
            "--runtime",
            "conda-pack",
            "--package",
            str(package),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "runtime conda-pack: OK" in output
    assert "package: OK" in output


def test_validate_rejects_a_malformed_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_nc_project(tmp_path / "nc-plugin")
    malformed = tmp_path / "broken.pypkg"
    malformed.write_bytes(b"this is not a zstd stream")

    exit_code = main(
        ["validate", str(tmp_path / "nc-plugin"), "--package", str(malformed)]
    )

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert stderr.startswith("validate: package: ")


def test_validate_rejects_checksum_corruption(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Merely listing a member cannot substitute for verifying its actual bytes."""
    _write_nc_project(tmp_path / "nc-plugin")
    package = tmp_path / "tampered.pypkg"
    _craft_package(
        package, checksum_overrides={"plugin/main.py": "0" * 64}
    )

    exit_code = main(
        ["validate", str(tmp_path / "nc-plugin"), "--package", str(package)]
    )

    assert exit_code == 1
    assert "plugin/main.py" in capsys.readouterr().err


def test_validate_rejects_members_outside_the_layout_whitelist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An extra top-level member could smuggle files past the accepted layout."""
    _write_nc_project(tmp_path / "nc-plugin")
    package = tmp_path / "extra.pypkg"
    _craft_package(package, extra_members={"extra.txt": b"x"})

    exit_code = main(
        ["validate", str(tmp_path / "nc-plugin"), "--package", str(package)]
    )

    assert exit_code == 1
    assert "outside the layout whitelist: extra.txt" in capsys.readouterr().err


def test_validate_rejects_escape_path_members(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Traversal member names could write outside the extraction root."""
    _write_nc_project(tmp_path / "nc-plugin")
    package = tmp_path / "escape.pypkg"
    _craft_package(package, extra_members={"../escape.py": b"x"})

    exit_code = main(
        ["validate", str(tmp_path / "nc-plugin"), "--package", str(package)]
    )

    assert exit_code == 1
    assert "unsafe path" in capsys.readouterr().err


def test_validate_usage_errors_exit_two(
    tmp_path: Path,
) -> None:
    """The CLI contract reserves exit code 2 for malformed command lines."""
    with pytest.raises(SystemExit) as missing_project:
        main(["validate"])
    assert missing_project.value.code == 2

    _write_nc_project(tmp_path / "nc-plugin")
    with pytest.raises(SystemExit) as bad_runtime:
        main(["validate", str(tmp_path / "nc-plugin"), "--runtime", "process"])
    assert bad_runtime.value.code == 2
