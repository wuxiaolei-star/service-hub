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
