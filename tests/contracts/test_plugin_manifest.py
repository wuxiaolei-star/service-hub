from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from python_hub_contracts import PluginManifest, load_plugin_manifest

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def valid_manifest_data() -> dict[str, Any]:
    return load_plugin_manifest(FIXTURES / "valid-plugin.yaml").model_dump()


def test_design_nc_to_shp_manifest_validates() -> None:
    manifest = load_plugin_manifest(FIXTURES / "valid-plugin.yaml")

    assert manifest.spec_version == "1.0"
    assert manifest.plugin.id == "nc_to_shp"
    assert manifest.plugin.version == "1.0.0"
    assert manifest.runtime.python.version == "3.10"
    assert manifest.parameter_by_name("method").default == "nearest"
    assert manifest.input_by_name("nc_file").max_size == 10_737_418_240
    assert manifest.output_by_name("result_files").type == "files"


@pytest.mark.parametrize("section", ["parameters", "inputs", "outputs"])
def test_named_manifest_sections_reject_duplicate_names(
    section: str, valid_manifest_data: dict[str, Any]
) -> None:
    duplicate = deepcopy(valid_manifest_data[section][0])
    valid_manifest_data[section].append(duplicate)

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_named_manifest_lookups_raise_key_error_when_absent() -> None:
    manifest = load_plugin_manifest(FIXTURES / "valid-plugin.yaml")

    with pytest.raises(KeyError):
        manifest.parameter_by_name("missing")
    with pytest.raises(KeyError):
        manifest.input_by_name("missing")
    with pytest.raises(KeyError):
        manifest.output_by_name("missing")


def test_unsupported_parameter_type_is_rejected(valid_manifest_data: dict[str, Any]) -> None:
    valid_manifest_data["parameters"][0]["type"] = "array"

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_unsupported_runtime_type_is_rejected(valid_manifest_data: dict[str, Any]) -> None:
    valid_manifest_data["runtime"]["type"] = "docker"

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


@pytest.mark.parametrize(
    ("options", "default"),
    [([], "nearest"), (["nearest", "nearest"], "nearest"), (["linear"], "nearest")],
)
def test_enum_options_are_non_empty_unique_and_contain_the_default(
    options: list[str], default: str, valid_manifest_data: dict[str, Any]
) -> None:
    parameter = valid_manifest_data["parameters"][3]
    parameter["options"] = options
    parameter["default"] = default

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_non_enum_parameter_rejects_options(valid_manifest_data: dict[str, Any]) -> None:
    valid_manifest_data["parameters"][0]["options"] = ["1", "2"]

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_required_parameter_cannot_define_a_default(valid_manifest_data: dict[str, Any]) -> None:
    valid_manifest_data["parameters"][0]["required"] = True

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_required_parameter_without_default_round_trips(
    valid_manifest_data: dict[str, Any]
) -> None:
    parameter = valid_manifest_data["parameters"][1]
    parameter["required"] = True
    parameter.pop("default", None)
    manifest = PluginManifest.model_validate(valid_manifest_data)

    round_tripped = PluginManifest.model_validate(manifest.model_dump())

    assert round_tripped.parameter_by_name("end_time").required is True


def test_numeric_minimum_cannot_exceed_maximum(valid_manifest_data: dict[str, Any]) -> None:
    parameter = valid_manifest_data["parameters"][0]
    parameter["min"] = 10
    parameter["max"] = 5

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


@pytest.mark.parametrize("field", ["min_count", "max_count", "max_size"])
def test_file_counts_and_sizes_must_be_positive(
    field: str, valid_manifest_data: dict[str, Any]
) -> None:
    valid_manifest_data["inputs"][0][field] = 0

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_manifest_models_are_strict_and_frozen(valid_manifest_data: dict[str, Any]) -> None:
    valid_manifest_data["unexpected"] = True
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)

    manifest = load_plugin_manifest(FIXTURES / "valid-plugin.yaml")
    with pytest.raises(ValidationError):
        manifest.plugin.name = "changed"
