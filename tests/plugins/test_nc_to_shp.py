from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import zipfile
from pathlib import Path

import h5py
import numpy as np
import pytest
from nc_to_shp_plugin.convert import map_cell_values_to_points
from nc_to_shp_plugin.main import run
from python_hub_sdk import InputFile, PluginContext, PluginValidationError
from scripts.compare_shapefile_zip import compare_archives


class _Events:
    def __init__(self) -> None:
        self.values: list[tuple[int, str | None]] = []

    def emit_progress(self, percent: int, message: str | None) -> None:
        self.values.append((percent, message))


@pytest.fixture
def sample_nc(tmp_path: Path) -> Path:
    path = tmp_path / "sample.nc"
    with h5py.File(path, "w") as nc_file:
        group = nc_file.create_group("1")
        group.create_dataset(
            "xyzgeo",
            data=np.asarray(
                [
                    [120.0, 30.0, 1.0],
                    [121.0, 30.0, 2.0],
                    [121.0, 31.0, 3.0],
                    [120.0, 31.0, 4.0],
                    [122.0, 32.0, 5.0],
                ]
            ),
        )
        group.create_dataset("cells", data=np.asarray([[0, 1, 2], [1, 2, 3]]))
        group.create_dataset("depth", data=np.asarray([[2.0, 4.0], [9999.0, 8.0]]))
        group.create_dataset("stage", data=np.asarray([[10.0, 14.0], [12.0, np.nan]]))
    return path


@pytest.fixture
def plugin_context(tmp_path: Path) -> PluginContext:
    for directory in ("input", "work", "output"):
        (tmp_path / directory).mkdir()
    return PluginContext(
        job_id="job_test",
        plugin_id="nc_to_shp",
        plugin_version="1.0.0",
        input_dir=tmp_path / "input",
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "output",
        logger=logging.getLogger("nc-to-shp-test"),
        event_sink=_Events(),
        cancellation_probe=lambda: False,
    )


def _input_file(source: Path, context: PluginContext) -> InputFile:
    target = context.input_dir / source.name
    target.write_bytes(source.read_bytes())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return InputFile(
        id="file_source",
        name=target.name,
        path=f"input/{target.name}",
        size=target.stat().st_size,
        extension=".nc",
        sha256=digest,
    )


def _valid_params() -> dict[str, object]:
    return {
        "group_name": "1",
        "metrics": ["depth", "stage"],
        "start_time": 1,
        "end_time": 2,
        "target_crs": None,
    }


def test_cell_values_average_only_valid_associations() -> None:
    """Averaging invalid cells as zero would corrupt shared and isolated vertices."""
    cells = np.asarray([[0, 1, 2], [1, 2, 3]])
    values = np.asarray([[2.0, 4.0], [9999.0, 8.0]])

    actual = map_cell_values_to_points(cells, values, point_count=5)

    np.testing.assert_allclose(
        actual,
        np.asarray(
            [
                [2.0, -9999.0],
                [3.0, 8.0],
                [3.0, 8.0],
                [4.0, 8.0],
                [-9999.0, -9999.0],
            ]
        ),
    )


def test_converter_rejects_unknown_group(
    sample_nc: Path, plugin_context: PluginContext
) -> None:
    """Silently selecting another group would convert the wrong model result."""
    source = _input_file(sample_nc, plugin_context)

    with pytest.raises(PluginValidationError) as raised:
        run(
            {**_valid_params(), "group_name": "missing"},
            {"source_nc": source},
            plugin_context,
        )

    assert raised.value.code == "NC_GROUP_NOT_FOUND"


def test_converter_rejects_time_range_beyond_selected_metric(
    sample_nc: Path, plugin_context: PluginContext
) -> None:
    """Clamping an out-of-range request would make requested and emitted data disagree."""
    source = _input_file(sample_nc, plugin_context)

    with pytest.raises(PluginValidationError) as raised:
        run(
            {**_valid_params(), "end_time": 3},
            {"source_nc": source},
            plugin_context,
        )

    assert raised.value.code == "NC_TIME_RANGE_INVALID"


@pytest.mark.skipif(
    importlib.util.find_spec("osgeo") is None,
    reason="GDAL/OGR is unavailable",
)
def test_converter_writes_requested_shapefiles_and_manifest(
    sample_nc: Path, plugin_context: PluginContext
) -> None:
    """Missing components or provenance would make a successful output unusable."""
    source = _input_file(sample_nc, plugin_context)

    result = run(_valid_params(), {"source_nc": source}, plugin_context)

    archive = plugin_context.output_file(result.files[0].path)
    with zipfile.ZipFile(archive) as output_zip:
        assert set(output_zip.namelist()) == {
            "depth_1-2.dbf",
            "depth_1-2.prj",
            "depth_1-2.shp",
            "depth_1-2.shx",
            "stage_1-2.dbf",
            "stage_1-2.prj",
            "stage_1-2.shp",
            "stage_1-2.shx",
            "manifest.json",
        }
        manifest = json.loads(output_zip.read("manifest.json"))
    assert manifest["source"]["sha256"] == source.sha256
    assert manifest["parameters"] == _valid_params()
    assert manifest["crs"] == "EPSG:4326"
    assert manifest["feature_count"] == 5
    assert result.data == {"feature_count": 5, "metrics": ["depth", "stage"]}
    assert compare_archives(archive, archive) == []
