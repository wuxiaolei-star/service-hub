from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest
import zstandard

# The NC plugin's packaging wrapper lives in its scripts/ package (see the
# conformance harness note in test_nc_to_shp.py for the path-free alternative).
PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "packages" / "nc-to-shp-plugin"
sys.path.insert(0, str(PLUGIN_ROOT))

from hub_server.services.archives import PluginArchiveService  # noqa: E402
from python_hub_contracts import PluginBuildManifest  # noqa: E402
from scripts.package_build import build_package  # noqa: E402


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


@pytest.mark.parametrize("runtime_type", ["conda-pack", "docker"])
def test_package_build_writes_strict_runtime_specific_package(
    tmp_path: Path, runtime_type: str
) -> None:
    """Putting runtime metadata at the wrong path would make Hub reject the Build."""
    runtime = tmp_path / f"{runtime_type}.tar.zst"
    runtime.write_bytes(b"prepared target-platform runtime")

    package = build_package(
        runtime_type=runtime_type,
        arch="amd64",
        output_dir=tmp_path / "out",
        prepared_runtime=runtime,
        docker_digest="1" * 64 if runtime_type == "docker" else None,
    )

    members = _members(package)
    build = PluginBuildManifest.model_validate_json(members["build.json"])
    expected_runtime_path = (
        "runtime/env.tar.zst" if runtime_type == "conda-pack" else "image.tar.zst"
    )
    assert build.runtime.type == runtime_type
    assert build.runtime.archive == expected_runtime_path
    assert members[expected_runtime_path] == runtime.read_bytes()
    checksums = json.loads(members["checksums.json"])
    assert set(checksums) == set(members) - {"checksums.json"}
    assert all(
        checksums[name] == hashlib.sha256(content).hexdigest()
        for name, content in members.items()
        if name != "checksums.json"
    )
    verified = PluginArchiveService(tmp_path / "data").verify_and_install(
        io.BytesIO(package.read_bytes()), hashlib.sha256(package.read_bytes()).hexdigest()
    )
    assert verified.build == build


def test_package_build_is_reproducible_for_fixed_inputs(tmp_path: Path) -> None:
    """Host timestamps in tar metadata would make identical offline Builds differ."""
    runtime = tmp_path / "image.tar.zst"
    runtime.write_bytes(b"prepared image archive")

    first = build_package(
        runtime_type="docker",
        arch="amd64",
        output_dir=tmp_path / "first",
        prepared_runtime=runtime,
        docker_digest="2" * 64,
        source_date_epoch=1_700_000_000,
    )
    second = build_package(
        runtime_type="docker",
        arch="amd64",
        output_dir=tmp_path / "second",
        prepared_runtime=runtime,
        docker_digest="2" * 64,
        source_date_epoch=1_700_000_000,
    )

    assert first.read_bytes() == second.read_bytes()


def test_nc_wrapper_preserves_legacy_source_digest_basis(tmp_path: Path) -> None:
    """Changing the NC digest basis would fork metadata for the same legacy package."""
    runtime = tmp_path / "env.tar.zst"
    runtime.write_bytes(b"prepared target-platform runtime")

    package = build_package(
        runtime_type="conda-pack",
        arch="amd64",
        output_dir=tmp_path / "out",
        prepared_runtime=runtime,
    )

    members = _members(package)
    build = PluginBuildManifest.model_validate_json(members["build.json"])
    legacy_root = Path("packages/nc-to-shp-plugin/src/nc_to_shp_plugin")
    digest = hashlib.sha256()
    for path in sorted(legacy_root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            digest.update(path.relative_to(legacy_root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")

    assert build.source_sha256 == digest.hexdigest()
