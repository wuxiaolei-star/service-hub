"""HDF5 validation, sparse cell-to-vertex mapping, and OGR output."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NoReturn, cast

import h5py  # type: ignore[import-untyped]
import numpy as np
from numpy.typing import NDArray
from python_hub_sdk import PluginExecutionError, PluginValidationError
from scipy import sparse  # type: ignore[import-untyped]

INVALID_VALUE: Final = 9999.0
OUTPUT_INVALID_VALUE: Final = -9999.0
_EPSG_PATTERN = re.compile(r"^EPSG:([1-9][0-9]*)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ConversionSummary:
    feature_count: int
    crs: str
    components: list[Path]


def map_cell_values_to_points(
    cells: NDArray[np.generic],
    cell_values: NDArray[np.floating[Any]],
    *,
    point_count: int,
) -> NDArray[np.float64]:
    """Average each time-by-cell matrix onto related vertices, ignoring invalid cells."""
    if cells.ndim != 2 or cell_values.ndim != 2:
        raise ValueError("cells and cell_values must be two-dimensional")
    if point_count <= 0 or cell_values.shape[1] != cells.shape[0]:
        raise ValueError("cell topology and metric dimensions do not match")

    rows: list[int] = []
    columns: list[int] = []
    for cell_index, raw_nodes in enumerate(cells):
        for point_index in sorted({int(node) for node in raw_nodes if int(node) >= 0}):
            if point_index >= point_count:
                raise ValueError("cell references a point outside the coordinate array")
            rows.append(point_index)
            columns.append(cell_index)
    relation = sparse.coo_matrix(
        (np.ones(len(rows), dtype=np.float64), (rows, columns)),
        shape=(point_count, cells.shape[0]),
    ).tocsr()
    invalid = np.isnan(cell_values) | np.isclose(
        cell_values, INVALID_VALUE, rtol=1e-3, atol=0.0
    )
    valid = ~invalid
    numerator = relation @ np.where(invalid, 0.0, cell_values).T
    denominator = relation @ valid.T
    return cast(
        NDArray[np.float64],
        np.divide(
            numerator,
            denominator,
            out=np.full(numerator.shape, OUTPUT_INVALID_VALUE, dtype=np.float64),
            where=denominator > 0,
        ),
    )


def convert_nc(
    *,
    source: Path,
    output_dir: Path,
    group_name: str,
    metrics: Sequence[str],
    start_time: int,
    end_time: int,
    target_crs: str | None,
    check_cancelled: Callable[[], None],
    progress: Callable[[int, str], None],
) -> ConversionSummary:
    """Validate and convert selected metric/time slices into PointZ Shapefiles."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with h5py.File(source, "r") as nc_file:
        if group_name not in nc_file or not isinstance(nc_file[group_name], h5py.Group):
            _invalid(
                "NC_GROUP_NOT_FOUND",
                "NC 文件中不存在指定 Group",
                {"group_name": group_name, "available": sorted(nc_file.keys())},
            )
        group = nc_file[group_name]
        assert isinstance(group, h5py.Group)
        coordinate_name = "xyzgeo" if "xyzgeo" in group else "xyz" if "xyz" in group else None
        if coordinate_name is None:
            _invalid("NC_COORDINATES_NOT_FOUND", "Group 中不存在 xyzgeo 或 xyz")
        if "cells" not in group:
            _invalid("NC_CELLS_NOT_FOUND", "Group 中不存在 cells")

        xyz = np.asarray(group[coordinate_name], dtype=np.float64)
        cells = np.asarray(group["cells"])
        if xyz.ndim != 2 or xyz.shape[0] == 0 or xyz.shape[1] < 2:
            _invalid("NC_COORDINATES_INVALID", "坐标数组必须为非空 point x 2/3 矩阵")
        if not np.isfinite(xyz[:, :2]).all():
            _invalid("NC_COORDINATES_INVALID", "坐标数组包含无效 XY 值")
        if cells.ndim != 2 or cells.shape[0] == 0:
            _invalid("NC_CELLS_INVALID", "cells 必须为非空二维矩阵")
        if not np.issubdtype(cells.dtype, np.integer):
            _invalid("NC_CELLS_INVALID", "cells 必须包含整数节点索引")

        metric_values: dict[str, NDArray[np.float64]] = {}
        for metric in metrics:
            check_cancelled()
            if metric not in group:
                _invalid(
                    "NC_METRIC_NOT_FOUND",
                    "Group 中不存在请求的指标",
                    {"metric": metric},
                )
            dataset = group[metric]
            if not isinstance(dataset, h5py.Dataset) or dataset.ndim != 2:
                _invalid(
                    "NC_METRIC_INVALID",
                    "指标必须为 time x cell 二维数组",
                    {"metric": metric},
                )
            if dataset.shape[1] != cells.shape[0]:
                _invalid(
                    "NC_METRIC_CELL_COUNT_MISMATCH",
                    "指标面数量与 cells 不一致",
                    {"metric": metric},
                )
            if start_time < 1 or end_time < start_time or end_time > dataset.shape[0]:
                _invalid(
                    "NC_TIME_RANGE_INVALID",
                    "请求时刻超出指标范围",
                    {"metric": metric, "available_times": dataset.shape[0]},
                )
            metric_values[metric] = np.asarray(
                dataset[start_time - 1 : end_time, :], dtype=np.float64
            )

        try:
            transformed_xyz, spatial_reference, crs = _coordinates(xyz, target_crs)
            components: list[Path] = []
            for index, (metric, values) in enumerate(metric_values.items()):
                check_cancelled()
                try:
                    point_values = map_cell_values_to_points(
                        cells, values, point_count=xyz.shape[0]
                    )
                except ValueError as error:
                    _invalid("NC_CELLS_INVALID", str(error), {"metric": metric})
                stem = f"{metric}_{start_time}-{end_time}"
                shapefile = output_dir / f"{stem}.shp"
                _write_pointz(
                    shapefile,
                    transformed_xyz,
                    point_values,
                    start_time,
                    spatial_reference,
                    check_cancelled,
                )
                metric_components = [
                    output_dir / f"{stem}{suffix}"
                    for suffix in (".shp", ".shx", ".dbf", ".prj")
                ]
                missing = [path.name for path in metric_components if not path.is_file()]
                if missing:
                    raise PluginExecutionError(
                        code="SHAPEFILE_COMPONENT_MISSING",
                        message="OGR 未生成完整 Shapefile",
                        details={"missing": missing},
                    )
                components.extend(metric_components)
                progress(20 + int(65 * (index + 1) / len(metric_values)), f"已完成 {metric}")
        except PluginValidationError:
            raise
        except PluginExecutionError:
            raise
        except Exception as error:
            raise PluginExecutionError(
                code="SHAPEFILE_WRITE_FAILED",
                message="写入 Shapefile 失败",
                details={"type": type(error).__name__},
            ) from error

    return ConversionSummary(feature_count=xyz.shape[0], crs=crs, components=components)


def _coordinates(
    xyz: NDArray[np.float64], target_crs: str | None
) -> tuple[NDArray[np.float64], Any, str]:
    from osgeo import osr  # type: ignore[import-not-found]

    source_srs = osr.SpatialReference()
    if source_srs.ImportFromEPSG(4326) != 0:
        raise RuntimeError("cannot load EPSG:4326")
    source_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    if target_crs is None:
        return xyz.copy(), source_srs, "EPSG:4326"
    match = _EPSG_PATTERN.fullmatch(target_crs)
    if match is None:
        _invalid("TARGET_CRS_INVALID", "target_crs 必须为 EPSG:<code> 或 null")
    normalized = f"EPSG:{int(match.group(1))}"
    target_srs = osr.SpatialReference()
    if target_srs.ImportFromEPSG(int(match.group(1))) != 0:
        _invalid("TARGET_CRS_INVALID", "无法加载目标 EPSG 坐标系", {"target_crs": normalized})
    target_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source_srs, target_srs)
    transformed = np.asarray(transform.TransformPoints(xyz.tolist()), dtype=np.float64)
    return transformed[:, : xyz.shape[1]], target_srs, normalized


def _write_pointz(
    path: Path,
    xyz: NDArray[np.float64],
    values: NDArray[np.float64],
    start_time: int,
    spatial_reference: Any,
    check_cancelled: Callable[[], None],
) -> None:
    from osgeo import ogr

    driver = ogr.GetDriverByName("ESRI Shapefile")
    if driver is None:
        raise RuntimeError("ESRI Shapefile driver is unavailable")
    if path.exists():
        driver.DeleteDataSource(str(path))
    data_source = driver.CreateDataSource(str(path))
    if data_source is None:
        raise RuntimeError("cannot create Shapefile datasource")
    layer = data_source.CreateLayer(path.stem, srs=spatial_reference, geom_type=ogr.wkbPoint25D)
    if layer is None:
        raise RuntimeError("cannot create PointZ layer")
    if layer.CreateField(ogr.FieldDefn("SMID", ogr.OFTInteger64)) != ogr.OGRERR_NONE:
        raise RuntimeError("cannot create SMID field")
    field_names: list[str] = []
    for offset in range(values.shape[1]):
        field_name = f"time{start_time + offset}"
        if len(field_name) > 10:
            raise ValueError("time field exceeds Shapefile name limit")
        definition = ogr.FieldDefn(field_name, ogr.OFTReal)
        definition.SetWidth(20)
        definition.SetPrecision(6)
        if layer.CreateField(definition) != ogr.OGRERR_NONE:
            raise RuntimeError(f"cannot create {field_name} field")
        field_names.append(field_name)
    definition = layer.GetLayerDefn()
    layer.StartTransaction()
    try:
        for point_index in range(xyz.shape[0]):
            if point_index % 1024 == 0:
                check_cancelled()
            feature = ogr.Feature(definition)
            feature.SetField("SMID", point_index + 1)
            for time_index, field_name in enumerate(field_names):
                feature.SetField(field_name, float(values[point_index, time_index]))
            geometry = ogr.Geometry(ogr.wkbPoint25D)
            z_coordinate = xyz[point_index, 2] if xyz.shape[1] >= 3 else 0.0
            geometry.AddPoint(
                float(xyz[point_index, 0]),
                float(xyz[point_index, 1]),
                float(z_coordinate),
            )
            feature.SetGeometry(geometry)
            if layer.CreateFeature(feature) != ogr.OGRERR_NONE:
                raise RuntimeError(f"cannot create point feature {point_index + 1}")
            feature = None
            geometry = None
        layer.CommitTransaction()
    except Exception:
        layer.RollbackTransaction()
        raise
    finally:
        layer = None
        data_source = None


def _invalid(code: str, message: str, details: object = None) -> NoReturn:
    raise PluginValidationError(code=code, message=message, details=details)
