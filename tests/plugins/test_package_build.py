from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
import zstandard
from hub_server.services.archives import PluginArchiveService
from python_hub_contracts import PluginBuildManifest
from scripts.package_build import build_package


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
