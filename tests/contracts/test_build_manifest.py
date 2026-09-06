import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from python_hub_contracts import PluginBuildManifest, PluginManifest, load_plugin_manifest

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def valid_build_data() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "valid-build.json").read_text("utf-8"))
    return data


@pytest.fixture
def plugin() -> PluginManifest:
    return load_plugin_manifest(FIXTURES / "valid-plugin.yaml")


def test_valid_build_manifest_matches_plugin(
    valid_build_data: dict[str, Any], plugin: PluginManifest
) -> None:
    build = PluginBuildManifest.model_validate(valid_build_data)

    assert build.target.os == "linux"
    assert build.target.arch == "arm64"
    assert build.runtime.archive == "runtime/env.tar.zst"
    build.assert_matches_plugin(plugin)


def test_valid_docker_build_manifest_matches_plugin(plugin: PluginManifest) -> None:
    data = {
        "schema_version": "1.0",
        "build_id": "build_docker_1",
        "plugin_id": "nc_to_shp",
        "plugin_version": "1.0.0",
        "target": {"os": "linux", "arch": "amd64"},
        "python_version": "3.10",
        "sdk_version": "1.0.0",
        "source_sha256": "a" * 64,
        "built_at": "2026-09-06T08:00:00+00:00",
        "runtime": {
            "type": "docker",
            "archive": "image.tar.zst",
            "image": "nc_to_shp:1.0.0-linux-amd64",
            "digest": "b" * 64,
        },
    }

    build = PluginBuildManifest.model_validate(data)

    assert build.runtime.type == "docker"
    build.assert_matches_plugin(plugin)


@pytest.mark.parametrize(
    ("os_name", "arch", "expected_arch"),
    [("Linux", "x86_64", "amd64"), ("gnu/linux", "aarch64", "arm64")],
)
def test_supported_build_platform_aliases_are_normalized(
    os_name: str, arch: str, expected_arch: str, valid_build_data: dict[str, Any]
) -> None:
    valid_build_data["target"] = {"os": os_name, "arch": arch}

    build = PluginBuildManifest.model_validate(valid_build_data)

    assert build.target.os == "linux"
    assert build.target.arch == expected_arch


@pytest.mark.parametrize(
    ("os_name", "arch"), [("windows", "amd64"), ("linux", "riscv64")]
)
def test_unsupported_build_platform_is_rejected(
    os_name: str, arch: str, valid_build_data: dict[str, Any]
) -> None:
    valid_build_data["target"] = {"os": os_name, "arch": arch}

    with pytest.raises(ValidationError):
        PluginBuildManifest.model_validate(valid_build_data)


def test_build_schema_version_is_exactly_v1(valid_build_data: dict[str, Any]) -> None:
    valid_build_data["schema_version"] = "2.0"

    with pytest.raises(ValidationError):
        PluginBuildManifest.model_validate(valid_build_data)


def test_build_timestamp_requires_timezone(valid_build_data: dict[str, Any]) -> None:
    valid_build_data["built_at"] = "2026-09-04T12:00:00"

    with pytest.raises(ValidationError):
        PluginBuildManifest.model_validate(valid_build_data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("archive", "/tmp/env.tar.zst"),
        ("archive", "runtime/../env.tar.zst"),
        ("fingerprint", "A" * 64),
        ("fingerprint", "a" * 63),
    ],
)
def test_conda_pack_runtime_rejects_unsafe_paths_and_invalid_hashes(
    field: str, value: str, valid_build_data: dict[str, Any]
) -> None:
    valid_build_data["runtime"][field] = value

    with pytest.raises(ValidationError):
        PluginBuildManifest.model_validate(valid_build_data)


def test_source_hash_must_be_lowercase_sha256(valid_build_data: dict[str, Any]) -> None:
    valid_build_data["source_sha256"] = "not-a-sha256"

    with pytest.raises(ValidationError):
        PluginBuildManifest.model_validate(valid_build_data)


@pytest.mark.parametrize(
    ("field", "value"),
    [("plugin_id", "other_plugin"), ("plugin_version", "2.0.0")],
)
def test_build_identity_mismatch_is_checked_cross_file(
    field: str, value: str, valid_build_data: dict[str, Any], plugin: PluginManifest
) -> None:
    valid_build_data[field] = value
    build = PluginBuildManifest.model_validate(valid_build_data)

    with pytest.raises(ValueError):
        build.assert_matches_plugin(plugin)


def test_build_python_version_mismatch_is_rejected_cross_file(
    valid_build_data: dict[str, Any], plugin: PluginManifest
) -> None:
    valid_build_data["python_version"] = "3.12"
    build = PluginBuildManifest.model_validate(valid_build_data)

    with pytest.raises(ValueError):
        build.assert_matches_plugin(plugin)


def test_build_sdk_major_version_mismatch_is_rejected_cross_file(
    valid_build_data: dict[str, Any], plugin: PluginManifest
) -> None:
    valid_build_data["sdk_version"] = "2.0.0"
    build = PluginBuildManifest.model_validate(valid_build_data)

    with pytest.raises(ValueError):
        build.assert_matches_plugin(plugin)


def test_build_sdk_minor_version_may_differ(
    valid_build_data: dict[str, Any], plugin: PluginManifest
) -> None:
    changed = deepcopy(valid_build_data)
    changed["sdk_version"] = "1.9.0"

    PluginBuildManifest.model_validate(changed).assert_matches_plugin(plugin)
