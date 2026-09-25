"""The parametrized plugin conformance suite (review plan G3 / B3).

Each case proves one plugin project behaves identically across independent runs
through the real runner protocol (``hub_publisher.dev``, python mode). The
execution backend and the built-in hooks live in ``harness.py``; onboarding a
plugin means adding one ``ConformanceCase`` below.

Case status on this repository:

- ``nc-to-shp``: needs the plugin-only heavy dependencies (h5py/numpy/scipy and
  GDAL) and skips otherwise, exactly like ``test_nc_to_shp.py``;
- ``docker-job-template``: pure stdlib, runs everywhere, and demonstrates that a
  second plugin joins the suite with zero harness changes.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import harness
import pytest
from harness import REPOSITORY_ROOT, ConformanceCase, PluginRun

NC_PARAMS: dict[str, object] = {
    "group_name": "1",
    "metrics": ["depth", "stage"],
    "start_time": 1,
    "end_time": 2,
    "target_crs": None,
}

NC_COMPONENTS = {
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


def nc_to_shp_case() -> ConformanceCase:
    """The NC plugin: a tiny synthetic NC converts into the expected archive."""
    return ConformanceCase(
        name="nc-to-shp",
        project_dir=REPOSITORY_ROOT / "packages" / "nc-to-shp-plugin",
        params=dict(NC_PARAMS),
        build_inputs=_build_nc_sample,
        assertions=_assert_nc_outputs,
        requires=("h5py", "numpy", "scipy", "osgeo"),
    )


def template_case() -> ConformanceCase:
    """The docker-job template plugin: deterministic text output, no inputs."""
    return ConformanceCase(
        name="docker-job-template",
        project_dir=REPOSITORY_ROOT / "templates" / "docker-job-plugin",
        params={"message": "conformance"},
        build_inputs=_build_no_inputs,
        assertions=_assert_template_outputs,
    )


def _build_no_inputs(inputs_dir: Path) -> list[Path]:
    return []


def _build_nc_sample(inputs_dir: Path) -> list[Path]:
    """Migrated from test_nc_to_shp.py's ``sample_nc`` fixture."""
    import h5py
    import numpy as np

    inputs_dir.mkdir(parents=True, exist_ok=True)
    path = inputs_dir / "sample.nc"
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
    return [path]


def _assert_nc_outputs(run: PluginRun) -> None:
    """Migrated from test_nc_to_shp.py's archive and manifest expectations."""
    assert len(run.result.files) == 1
    assert run.result.files[0].path == "nc_to_shp_result.zip"
    assert run.result.data == {"feature_count": 5, "metrics": ["depth", "stage"]}

    source = run.workspace / "input" / "sample.nc"
    archive = run.output_file("nc_to_shp_result.zip")
    with zipfile.ZipFile(archive) as output_zip:
        assert set(output_zip.namelist()) == NC_COMPONENTS
        manifest = json.loads(output_zip.read("manifest.json"))
    assert manifest["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["parameters"] == NC_PARAMS
    assert manifest["crs"] == "EPSG:4326"
    assert manifest["feature_count"] == 5


def _assert_template_outputs(run: PluginRun) -> None:
    assert len(run.result.files) == 1
    assert run.result.files[0].path == "result.txt"
    assert run.result.data == {"message": "conformance"}
    assert run.output_file("result.txt").read_text(encoding="utf-8") == "conformance\n"


CASES = [nc_to_shp_case(), template_case()]


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_plugin_conformance(case: ConformanceCase, tmp_path: Path) -> None:
    """Every registered plugin passes its conformance case end to end."""
    outcome = harness.run_case(case, tmp_path)

    assert outcome.runs
    assert all(run.result.status.value == "SUCCESS" for run in outcome.runs)
    if case.compare_runs:
        assert len(outcome.runs) == 2
        assert all(comparison.equal for comparison in outcome.comparisons.values())
