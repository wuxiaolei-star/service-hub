"""Service Hub entrypoint for the NC-to-Shapefile conversion."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn, cast

from python_hub_sdk import (
    InputFile,
    OutputFile,
    PluginContext,
    PluginResult,
    PluginValidationError,
)

from .convert import convert_nc

_OUTPUT_NAME = "nc_to_shp_result.zip"


def run(
    params: dict[str, object],
    inputs: dict[str, InputFile | list[InputFile]],
    context: PluginContext,
) -> PluginResult:
    """Convert one immutable NC input into a manifest-bearing Shapefile ZIP."""
    source_input = inputs.get("source_nc")
    if not isinstance(source_input, InputFile):
        _invalid("SOURCE_NC_REQUIRED", "source_nc 必须是单个输入文件")
    source_path = _input_path(context, source_input)
    _validate_input_integrity(source_path, source_input)
    group_name, metrics, start_time, end_time, target_crs = _parameters(params)

    context.progress(5, "正在校验 NC")
    work_dir = context.work_file("shapefiles")
    work_dir.mkdir(parents=True, exist_ok=True)
    summary = convert_nc(
        source=source_path,
        output_dir=work_dir,
        group_name=group_name,
        metrics=metrics,
        start_time=start_time,
        end_time=end_time,
        target_crs=target_crs,
        check_cancelled=context.check_cancelled,
        progress=context.progress,
    )
    manifest = {
        "schema_version": "1.0",
        "source": {
            "id": source_input.id,
            "name": source_input.name,
            "size": source_input.size,
            "sha256": source_input.sha256.lower(),
        },
        "parameters": {
            "group_name": group_name,
            "metrics": metrics,
            "start_time": start_time,
            "end_time": end_time,
            "target_crs": target_crs,
        },
        "crs": summary.crs,
        "feature_count": summary.feature_count,
        "components": sorted(path.name for path in summary.components),
    }
    archive = context.output_file(_OUTPUT_NAME, create_parent=True)
    _write_zip(archive, summary.components, manifest)
    context.progress(95, "输出 ZIP 已生成")
    return PluginResult(
        message="NC 转 Shapefile 完成",
        data={"feature_count": summary.feature_count, "metrics": metrics},
        files=[OutputFile(name="result_archive", path=_OUTPUT_NAME, format="application/zip")],
    )


def _parameters(
    raw: Mapping[str, object],
) -> tuple[str, list[str], int, int, str | None]:
    group_name = raw.get("group_name", "1")
    metrics = raw.get("metrics", ["depth", "stage"])
    start_time = raw.get("start_time", 1)
    end_time = raw.get("end_time", 20)
    target_crs = raw.get("target_crs")
    if not isinstance(group_name, str) or not group_name:
        _invalid("NC_GROUP_INVALID", "group_name 必须是非空字符串")
    if (
        not isinstance(metrics, list)
        or not metrics
        or any(not isinstance(metric, str) for metric in metrics)
        or len(metrics) != len(set(metrics))
        or any(metric not in {"depth", "stage"} for metric in metrics)
    ):
        _invalid("NC_METRICS_INVALID", "metrics 必须是 depth/stage 的非空无重复列表")
    if not isinstance(start_time, int) or isinstance(start_time, bool) or start_time < 1:
        _invalid("NC_TIME_RANGE_INVALID", "start_time 必须是不小于 1 的整数")
    if (
        not isinstance(end_time, int)
        or isinstance(end_time, bool)
        or end_time < start_time
    ):
        _invalid("NC_TIME_RANGE_INVALID", "end_time 必须是不小于 start_time 的整数")
    if target_crs is not None and not isinstance(target_crs, str):
        _invalid("TARGET_CRS_INVALID", "target_crs 必须是字符串或 null")
    return group_name, cast(list[str], metrics), start_time, end_time, target_crs


def _input_path(context: PluginContext, source: InputFile) -> Path:
    root = context.input_dir.resolve()
    candidate = (root / source.name).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise PluginValidationError(
            code="SOURCE_NC_PATH_INVALID", message="source_nc 文件名不安全"
        ) from error
    if not candidate.is_file():
        _invalid("SOURCE_NC_NOT_FOUND", "source_nc 文件不存在")
    return candidate


def _validate_input_integrity(path: Path, source: InputFile) -> None:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    if size != source.size or digest.hexdigest() != source.sha256.lower():
        _invalid("INPUT_INTEGRITY_MISMATCH", "source_nc 大小或 SHA256 不匹配")


def _write_zip(archive: Path, components: list[Path], manifest: dict[str, object]) -> None:
    timestamp = (1980, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for component in sorted(components, key=lambda path: path.name):
            info = zipfile.ZipInfo(component.name, timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, component.read_bytes())
        info = zipfile.ZipInfo("manifest.json", timestamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        output.writestr(
            info,
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            .encode("utf-8")
            + b"\n",
        )


def _invalid(code: str, message: str) -> NoReturn:
    raise PluginValidationError(code=code, message=message)
