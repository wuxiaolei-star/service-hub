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


def test_plugin_spec_version_is_exactly_v1(valid_manifest_data: dict[str, Any]) -> None:
    valid_manifest_data["spec_version"] = "2.0"

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_sdk_version_is_exactly_v1(valid_manifest_data: dict[str, Any]) -> None:
    valid_manifest_data["sdk"]["version"] = "2.0"

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


def test_string_list_parameter_accepts_bounded_allowed_default(
    valid_manifest_data: dict[str, Any],
) -> None:
    """Rejecting JSON arrays would make the documented metrics parameter impossible."""
    valid_manifest_data["parameters"].append(
        {
            "name": "metrics",
            "label": "Metrics",
            "type": "string_list",
            "required": False,
            "default": ["depth", "stage"],
            "options": ["depth", "stage"],
            "min": 1,
            "max": 2,
        }
    )

    manifest = PluginManifest.model_validate(valid_manifest_data)

    assert manifest.parameter_by_name("metrics").default == ["depth", "stage"]


@pytest.mark.parametrize(
    "default",
    [[], ["depth", 1], ["unknown"], ["depth", "depth"]],
)
def test_string_list_parameter_rejects_invalid_default(
    valid_manifest_data: dict[str, Any], default: object
) -> None:
    """An invalid manifest default would bypass request-time parameter validation."""
    valid_manifest_data["parameters"].append(
        {
            "name": "metrics",
            "label": "Metrics",
            "type": "string_list",
            "required": False,
            "default": default,
            "options": ["depth", "stage"],
            "min": 1,
            "max": 2,
        }
    )

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


def test_input_minimum_count_cannot_exceed_maximum(
    valid_manifest_data: dict[str, Any]
) -> None:
    input_spec = valid_manifest_data["inputs"][0]
    input_spec["min_count"] = 2
    input_spec["max_count"] = 1

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_required_environment_variable_names_must_be_unique(
    valid_manifest_data: dict[str, Any]
) -> None:
    valid_manifest_data["environment_variables"]["required"] = ["API_TOKEN", "API_TOKEN"]

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_manifest_models_are_strict_and_frozen(valid_manifest_data: dict[str, Any]) -> None:
    valid_manifest_data["unexpected"] = True
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)

    manifest = load_plugin_manifest(FIXTURES / "valid-plugin.yaml")
    with pytest.raises(ValidationError):
        manifest.plugin.name = "changed"


def test_manifest_accepts_documented_text_and_list_boundaries(
    valid_manifest_data: dict[str, Any]
) -> None:
    valid_manifest_data["plugin"]["name"] = "n" * 256
    valid_manifest_data["plugin"]["description"] = "d" * 4096
    valid_manifest_data["plugin"]["tags"] = [f"tag_{index}" for index in range(64)]
    valid_manifest_data["entrypoint"]["module"] = "m" * 256
    valid_manifest_data["entrypoint"]["function"] = "f" * 256
    valid_manifest_data["parameters"][0]["label"] = "l" * 256
    valid_manifest_data["inputs"][0]["label"] = "l" * 256
    valid_manifest_data["outputs"][0]["label"] = "l" * 256
    valid_manifest_data["environment_variables"]["required"] = [
        f"ENV_{index}" for index in range(64)
    ]

    manifest = PluginManifest.model_validate(valid_manifest_data)

    assert len(manifest.plugin.name) == 256
    assert len(manifest.plugin.description or "") == 4096
    assert len(manifest.plugin.tags) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "n" * 257),
        ("description", "d" * 4097),
        ("author", "a" * 257),
        ("category", "c" * 257),
    ],
)
def test_plugin_info_rejects_oversized_text(
    field: str, value: str, valid_manifest_data: dict[str, Any]
) -> None:
    valid_manifest_data["plugin"][field] = value

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_manifest_rejects_oversized_labels_and_entrypoint_names(
    valid_manifest_data: dict[str, Any]
) -> None:
    for section, field in (
        ("entrypoint", "module"),
        ("entrypoint", "function"),
        ("parameters", "label"),
        ("inputs", "label"),
        ("outputs", "label"),
    ):
        changed = deepcopy(valid_manifest_data)
        target = changed[section] if section == "entrypoint" else changed[section][0]
        target[field] = "x" * 257
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(changed)


@pytest.mark.parametrize("field", ["tags", "parameters", "inputs", "outputs"])
def test_manifest_rejects_more_than_64_list_entries(
    field: str, valid_manifest_data: dict[str, Any]
) -> None:
    if field == "tags":
        valid_manifest_data["plugin"][field] = [f"tag_{index}" for index in range(65)]
    else:
        template = valid_manifest_data[field][0]
        valid_manifest_data[field] = []
        for index in range(65):
            item = deepcopy(template)
            item["name"] = f"item_{index}"
            valid_manifest_data[field].append(item)

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_manifest_rejects_oversized_option_and_environment_lists(
    valid_manifest_data: dict[str, Any]
) -> None:
    options_manifest = deepcopy(valid_manifest_data)
    options_manifest["parameters"][3]["options"] = [
        f"option_{index}" for index in range(65)
    ]
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(options_manifest)

    valid_manifest_data["environment_variables"]["required"] = [
        f"ENV_{index}" for index in range(65)
    ]
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


@pytest.mark.parametrize(
    "default",
    [
        {1: "x"},
        {"value": float("nan")},
        {"value": tuple(range(2))},
        {str(index): index for index in range(65)},
    ],
)
def test_parameter_default_rejects_non_json_or_oversized_values(
    default: object, valid_manifest_data: dict[str, Any]
) -> None:
    valid_manifest_data["parameters"][0]["default"] = default

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_parameter_default_rejects_more_than_8_container_levels(
    valid_manifest_data: dict[str, Any]
) -> None:
    default: object = "leaf"
    for _ in range(9):
        default = [default]
    valid_manifest_data["parameters"][0]["default"] = default

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_execution_resource_limits_are_optional_and_default_to_none(
    valid_manifest_data: dict[str, Any],
) -> None:
    """B7/G7: v1.0 manifests without resource caps stay valid and unlimited."""
    manifest = PluginManifest.model_validate(valid_manifest_data)

    assert manifest.execution.memory_mb is None
    assert manifest.execution.cpus is None


def test_execution_accepts_declared_resource_limits(
    valid_manifest_data: dict[str, Any],
) -> None:
    valid_manifest_data["execution"]["memory_mb"] = 4096
    valid_manifest_data["execution"]["cpus"] = 2.5

    manifest = PluginManifest.model_validate(valid_manifest_data)

    assert manifest.execution.memory_mb == 4096
    assert manifest.execution.cpus == 2.5


@pytest.mark.parametrize("field", ["memory_mb", "cpus"])
@pytest.mark.parametrize("value", [0, -1])
def test_execution_rejects_non_positive_resource_limits(
    field: str, value: int, valid_manifest_data: dict[str, Any]
) -> None:
    valid_manifest_data["execution"][field] = value

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)


def test_execution_rejects_unknown_resource_limit_fields(
    valid_manifest_data: dict[str, Any],
) -> None:
    valid_manifest_data["execution"]["gpus"] = 1

    with pytest.raises(ValidationError):
        PluginManifest.model_validate(valid_manifest_data)
