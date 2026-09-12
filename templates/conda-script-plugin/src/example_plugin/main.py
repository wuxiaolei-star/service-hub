"""Minimal conda-pack script plugin: write the message parameter to result.txt."""

from __future__ import annotations

from python_hub_sdk import InputFile, OutputFile, PluginContext, PluginResult


def run(
    params: dict[str, object],
    inputs: dict[str, InputFile | list[InputFile]],
    context: PluginContext,
) -> PluginResult:
    """Copy the ``message`` parameter into ``result.txt`` inside the job output."""
    message = str(params.get("message", "hello"))
    destination = context.output_file("result.txt", create_parent=True)
    destination.write_text(message + "\n", encoding="utf-8")
    return PluginResult(
        message="处理完成",
        data={"message": message},
        files=[OutputFile(name="result", path="result.txt", format="text/plain")],
    )
