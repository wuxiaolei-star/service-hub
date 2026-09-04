import dataclasses

import pytest
from python_hub_sdk import InputFile, OutputFile, PluginResult


def _input_file() -> InputFile:
    return InputFile(
        id="file-1",
        name="input.txt",
        path="/tmp/input.txt",
        size=3,
        extension=".txt",
        sha256="a" * 64,
    )


def test_input_file_is_frozen_and_slot_based() -> None:
    value = _input_file()
    assert dataclasses.is_dataclass(value)
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.name = "changed"  # type: ignore[misc]
    assert not hasattr(value, "__dict__")


@pytest.mark.parametrize("field", ["id", "name", "path", "extension"])
def test_input_file_rejects_non_string_fields(field: str) -> None:
    values = dataclasses.asdict(_input_file())
    values[field] = 1
    with pytest.raises(ValueError):
        InputFile(**values)


def test_input_file_rejects_non_string_sha256() -> None:
    values = dataclasses.asdict(_input_file())
    values["sha256"] = 1
    with pytest.raises(ValueError):
        InputFile(**values)


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/result.txt",
        "../result.txt",
        "a/../../result.txt",
        "C:\\tmp\\result.txt",
        "..\\result.txt",
    ],
)
def test_output_file_rejects_absolute_or_traversal_path(path: str) -> None:
    with pytest.raises(ValueError):
        OutputFile(name="result", path=path)


@pytest.mark.parametrize("path", ["C:result.txt", "Z:folder/result.txt"])
def test_output_file_rejects_drive_relative_path(path: str) -> None:
    with pytest.raises(ValueError):
        OutputFile(name="result", path=path)


@pytest.mark.parametrize("name", [1, None])
def test_output_file_rejects_non_string_name(name: object) -> None:
    with pytest.raises(ValueError):
        OutputFile(name=name, path="result.txt")  # type: ignore[arg-type]


@pytest.mark.parametrize("format_value", [1, ""])
def test_output_file_rejects_invalid_format(format_value: object) -> None:
    with pytest.raises(ValueError):
        OutputFile(name="result", path="result.txt", format=format_value)  # type: ignore[arg-type]


def test_plugin_result_does_not_share_mutable_defaults() -> None:
    first = PluginResult()
    second = PluginResult()
    first.data["key"] = "value"
    first.files.append(OutputFile(name="out", path="out.txt"))
    assert second.data == {}
    assert second.files == []


def test_plugin_result_requires_json_serializable_data() -> None:
    with pytest.raises(ValueError):
        PluginResult(data={"bad": object()})


@pytest.mark.parametrize("message", [1, object()])
def test_plugin_result_rejects_invalid_message(message: object) -> None:
    with pytest.raises(ValueError):
        PluginResult(message=message)  # type: ignore[arg-type]


@pytest.mark.parametrize("data", [[], "text", None])
def test_plugin_result_rejects_non_dict_data(data: object) -> None:
    with pytest.raises(ValueError):
        PluginResult(data=data)  # type: ignore[arg-type]


@pytest.mark.parametrize("files", [None, {}, ["result.txt"]])
def test_plugin_result_rejects_non_output_files(files: object) -> None:
    with pytest.raises(ValueError):
        PluginResult(files=files)  # type: ignore[arg-type]
