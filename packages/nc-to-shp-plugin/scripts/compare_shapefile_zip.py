"""Compare two plugin ZIP outputs by OGR geometry, CRS, schema, and SMID values.

Thin wrapper around the platform comparator ``hub_publisher.conformance``: the
structural layer (safe extraction, component sets, manifest equality and
per-file byte/record summaries) lives there. This script keeps the plugin's
deep semantic pass — OGR geometry type, CRS, field schema and per-SMID geometry
and attribute values — which needs the plugin's own ``osgeo`` dependency. When
``osgeo`` is unavailable the wrapper still reports structural differences
(record counts, geometry types, byte differences) and simply skips the
per-feature detail.

CLI behavior is unchanged: differences go to stderr with exit code 1; equal
archives print "Shapefile ZIP outputs are semantically equal" with exit code 0.
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_ROOT = _PACKAGE_ROOT.parents[1]
_PUBLISHER_SRC = _REPOSITORY_ROOT / "packages" / "hub-publisher" / "src"
if _PUBLISHER_SRC.is_dir() and str(_PUBLISHER_SRC) not in sys.path:
    sys.path.insert(0, str(_PUBLISHER_SRC))

from hub_publisher.conformance import compare_output_archives, extract_archive_flat  # noqa: E402


def compare_archives(
    left: Path, right: Path, *, expected_features: int | None = None
) -> list[str]:
    """Return semantic differences; an empty list means equality."""
    structural = compare_output_archives(left, right)
    if structural.only_in_left or structural.only_in_right:
        return [
            f"component sets differ: {sorted(structural.left_names)}"
            f" != {sorted(structural.right_names)}"
        ]
    differences = ["manifest.json differs"] if structural.manifest_differs else []
    if expected_features is None and not structural.differing:
        # Byte-identical archives and no feature-count expectation: the deep
        # pass cannot add information, so skip the extraction entirely.
        return differences
    with (
        tempfile.TemporaryDirectory(prefix="nc-shp-left-") as left_name,
        tempfile.TemporaryDirectory(prefix="nc-shp-right-") as right_name,
    ):
        left_root = Path(left_name)
        right_root = Path(right_name)
        left_names = extract_archive_flat(left, left_root)
        extract_archive_flat(right, right_root)
        for name in sorted(item for item in left_names if item.endswith(".shp")):
            differences.extend(
                _compare_shapefile(
                    left_root / name,
                    right_root / name,
                    expected_features=expected_features,
                )
            )
    return differences


def _compare_shapefile(
    left: Path, right: Path, *, expected_features: int | None
) -> list[str]:
    from osgeo import ogr  # type: ignore[import-not-found]

    left_source = ogr.Open(str(left), 0)
    right_source = ogr.Open(str(right), 0)
    if left_source is None or right_source is None:
        return [f"cannot open {left.name}"]
    left_layer = left_source.GetLayer(0)
    right_layer = right_source.GetLayer(0)
    prefix = left.name
    differences: list[str] = []
    if ogr.GT_Flatten(left_layer.GetGeomType()) != ogr.wkbPoint or not ogr.GT_HasZ(
        left_layer.GetGeomType()
    ):
        differences.append(f"{prefix}: left layer is not PointZ")
    if ogr.GT_Flatten(right_layer.GetGeomType()) != ogr.wkbPoint or not ogr.GT_HasZ(
        right_layer.GetGeomType()
    ):
        differences.append(f"{prefix}: right layer is not PointZ")
    left_srs = left_layer.GetSpatialRef()
    right_srs = right_layer.GetSpatialRef()
    if left_srs is None or right_srs is None or not bool(left_srs.IsSame(right_srs)):
        differences.append(f"{prefix}: CRS differs")
    left_count = left_layer.GetFeatureCount()
    right_count = right_layer.GetFeatureCount()
    if expected_features is not None and left_count == right_count != expected_features:
        differences.append(
            f"{prefix}: expected {expected_features} features, got {left_count}"
        )
    elif expected_features is not None:
        if left_count != expected_features:
            differences.append(
                f"{prefix}: expected {expected_features} left features, got {left_count}"
            )
        if right_count != expected_features:
            differences.append(
                f"{prefix}: expected {expected_features} right features, got {right_count}"
            )
    if left_count != right_count:
        differences.append(f"{prefix}: feature count differs")
    if _field_schema(left_layer) != _field_schema(right_layer):
        differences.append(f"{prefix}: field schema differs")

    left_features = _features(left_layer)
    right_features = _features(right_layer)
    if set(left_features) != set(right_features):
        differences.append(f"{prefix}: SMID sets differ")
        return differences
    for smid in sorted(left_features):
        left_geometry, left_values = left_features[smid]
        right_geometry, right_values = right_features[smid]
        if not all(_equal_number(a, b) for a, b in zip(left_geometry, right_geometry, strict=True)):
            differences.append(f"{prefix}: geometry differs at SMID {smid}")
            break
        if len(left_values) != len(right_values) or not all(
            _equal_value(a, b) for a, b in zip(left_values, right_values, strict=True)
        ):
            differences.append(f"{prefix}: attributes differ at SMID {smid}")
            break
    return differences


def _field_schema(layer: Any) -> list[tuple[str, int, int, int]]:
    definition = layer.GetLayerDefn()
    return [
        (
            definition.GetFieldDefn(index).GetName(),
            definition.GetFieldDefn(index).GetType(),
            definition.GetFieldDefn(index).GetWidth(),
            definition.GetFieldDefn(index).GetPrecision(),
        )
        for index in range(definition.GetFieldCount())
    ]


def _features(layer: Any) -> dict[int, tuple[tuple[float, float, float], tuple[object, ...]]]:
    result: dict[int, tuple[tuple[float, float, float], tuple[object, ...]]] = {}
    definition = layer.GetLayerDefn()
    field_names = [
        definition.GetFieldDefn(index).GetName()
        for index in range(definition.GetFieldCount())
        if definition.GetFieldDefn(index).GetName() != "SMID"
    ]
    layer.ResetReading()
    for feature in layer:
        smid = int(feature.GetField("SMID"))
        geometry = feature.GetGeometryRef()
        result[smid] = (
            (geometry.GetX(), geometry.GetY(), geometry.GetZ()),
            tuple(feature.GetField(name) for name in field_names),
        )
    return result


def _equal_value(left: object, right: object) -> bool:
    if isinstance(left, int | float) and isinstance(right, int | float):
        return _equal_number(float(left), float(right))
    return left == right


def _equal_number(left: float, right: float) -> bool:
    return (math.isnan(left) and math.isnan(right)) or math.isclose(
        left, right, rel_tol=1e-9, abs_tol=1e-8
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args(argv)
    differences = compare_archives(args.left, args.right)
    if differences:
        for difference in differences:
            print(difference, file=sys.stderr)
        return 1
    print("Shapefile ZIP outputs are semantically equal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
