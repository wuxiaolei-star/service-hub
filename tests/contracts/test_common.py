import pytest
from pydantic import BaseModel, ValidationError
from python_hub_contracts.common import (
    PluginId,
    RelativeProtocolPath,
    SemanticVersion,
    StrictContractModel,
    normalize_arch,
    normalize_os,
)


class PluginReference(BaseModel):
    plugin_id: PluginId


class PathReference(BaseModel):
    path: RelativeProtocolPath


class VersionReference(BaseModel):
    version: SemanticVersion


class StrictReference(StrictContractModel):
    name: str


@pytest.mark.parametrize("value", ["nc_to_shp", "model_2d_export"])
def test_plugin_id_accepts_stable_ids(value: str) -> None:
    assert PluginReference(plugin_id=value).plugin_id == value


@pytest.mark.parametrize("value", ["NC_TO_SHP", "nc-to-shp", "中文", "ab"])
def test_plugin_id_rejects_invalid_ids(value: str) -> None:
    with pytest.raises(ValidationError):
        PluginReference(plugin_id=value)


@pytest.mark.parametrize("value", ["0.0.0", "1.2.3", "10.20.30"])
def test_semantic_version_accepts_core_versions(value: str) -> None:
    assert VersionReference(version=value).version == value


@pytest.mark.parametrize("value", ["1.2", "1.2.3.4", "v1.2.3", "01.2.3"])
def test_semantic_version_rejects_non_core_versions(value: str) -> None:
    with pytest.raises(ValidationError):
        VersionReference(version=value)


@pytest.mark.parametrize("value", ["/tmp/a", "C:/temp/a", "../a", "input/../secret"])
def test_relative_protocol_path_rejects_escape(value: str) -> None:
    with pytest.raises(ValidationError):
        PathReference(path=value)


def test_relative_protocol_path_normalizes_windows_separators() -> None:
    assert PathReference(path=r"input\subdir\file.txt").path == "input/subdir/file.txt"


@pytest.mark.parametrize("value", ["", ".", "input/./file", "input//file", "input/\x00file"])
def test_relative_protocol_path_rejects_invalid_segments(value: str) -> None:
    with pytest.raises(ValidationError):
        PathReference(path=value)


def test_strict_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        StrictReference(name="example", unexpected=True)


def test_platform_aliases_are_normalized() -> None:
    assert normalize_os("Linux") == "linux"
    assert normalize_arch("x86_64") == "amd64"
    assert normalize_arch("aarch64") == "arm64"
