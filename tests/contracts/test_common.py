import pytest
from pydantic import BaseModel, ValidationError
from python_hub_contracts.common import (
    PluginId,
    RelativeProtocolPath,
    StrictContractModel,
    normalize_arch,
    normalize_os,
)


class PluginReference(BaseModel):
    plugin_id: PluginId


class PathReference(BaseModel):
    path: RelativeProtocolPath


class StrictReference(StrictContractModel):
    name: str


@pytest.mark.parametrize("value", ["nc_to_shp", "model_2d_export"])
def test_plugin_id_accepts_stable_ids(value: str) -> None:
    assert PluginReference(plugin_id=value).plugin_id == value


@pytest.mark.parametrize("value", ["NC_TO_SHP", "nc-to-shp", "中文", "ab"])
def test_plugin_id_rejects_invalid_ids(value: str) -> None:
    with pytest.raises(ValidationError):
        PluginReference(plugin_id=value)


@pytest.mark.parametrize("value", ["/tmp/a", "C:/temp/a", "../a", "input/../secret"])
def test_relative_protocol_path_rejects_escape(value: str) -> None:
    with pytest.raises(ValidationError):
        PathReference(path=value)


def test_strict_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        StrictReference(name="example", unexpected=True)


def test_platform_aliases_are_normalized() -> None:
    assert normalize_os("Linux") == "linux"
    assert normalize_arch("x86_64") == "amd64"
    assert normalize_arch("aarch64") == "arm64"
