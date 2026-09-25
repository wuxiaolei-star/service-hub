"""Tests for ``hub_publisher.conformance`` — the platform output-arc comparator.

The fixtures hand-craft minimal dBASE and shapefile headers so the pure-stdlib
summary logic is testable without GDAL; the NC plugin's OGR-based deep pass is
covered by the plugin's own tests (skipped without osgeo, like the plugin).
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest
from hub_publisher.conformance import (
    compare_output_archives,
    extract_archive_flat,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# The NC wrapper must keep delegating to the platform module (review plan B3).
PLUGIN_ROOT = REPOSITORY_ROOT / "packages" / "nc-to-shp-plugin"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from scripts.compare_shapefile_zip import compare_archives as nc_compare_archives  # noqa: E402

MANIFEST = b'{"schema_version": "1.0"}\n'
DEFAULT_FIELDS = (("id", "N", 10, 0),)


def _dbf_bytes(
    record_count: int,
    fields: tuple[tuple[str, str, int, int], ...] = DEFAULT_FIELDS,
) -> bytes:
    descriptors = b"".join(
        name.encode("ascii").ljust(11, b"\x00")
        + type_.encode("ascii")
        + b"\x00" * 4
        + bytes([length, decimals])
        + b"\x00" * 14
        for name, type_, length, decimals in fields
    )
    header_length = 32 + len(descriptors) + 1
    header = (
        bytes([0x03])
        + b"\x00" * 3
        + record_count.to_bytes(4, "little")
        + header_length.to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + b"\x00" * 20
    )
    records = b"\x20" * record_count
    return header + descriptors + b"\x0d" + records


def _shp_bytes(shape_type: int) -> bytes:
    header = bytearray(100)
    header[0:4] = (9994).to_bytes(4, "big")
    header[28:32] = (100).to_bytes(4, "little")
    header[32:36] = shape_type.to_bytes(4, "little")
    return bytes(header)


def _archive(tmp_path: Path, name: str, members: dict[str, bytes]) -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
        for member_name, content in members.items():
            output.writestr(zipfile.ZipInfo(member_name, (1980, 1, 1, 0, 0, 0)), content)
    return path


def test_identical_archives_compare_equal(tmp_path: Path) -> None:
    members = {"manifest.json": MANIFEST, "data.dbf": _dbf_bytes(2)}
    left = _archive(tmp_path, "left.zip", members)
    right = _archive(tmp_path, "right.zip", members)

    comparison = compare_output_archives(left, right)

    assert comparison.equal
    assert comparison.failure_lines() == []
    assert comparison.differing == ()


def test_component_set_differences_are_structured(tmp_path: Path) -> None:
    left = _archive(
        tmp_path, "left.zip", {"manifest.json": MANIFEST, "extra.txt": b"x"}
    )
    right = _archive(tmp_path, "right.zip", {"manifest.json": MANIFEST})

    comparison = compare_output_archives(left, right)

    assert not comparison.equal
    assert comparison.only_in_left == ("extra.txt",)
    assert comparison.only_in_right == ()
    assert comparison.failure_lines() == [
        f"component sets differ: {['extra.txt', 'manifest.json']} != {['manifest.json']}"
    ]


def test_manifest_comparison_is_json_aware(tmp_path: Path) -> None:
    reformatted = b'{\n  "schema_version": "1.0"\n}\n'
    left = _archive(tmp_path, "left.zip", {"manifest.json": MANIFEST})
    same_json = _archive(tmp_path, "same.zip", {"manifest.json": reformatted})
    changed = _archive(
        tmp_path, "changed.zip", {"manifest.json": b'{"schema_version": "1.1"}\n'}
    )
    unparseable = _archive(tmp_path, "broken.zip", {"manifest.json": b"{broken"})

    assert not compare_output_archives(left, same_json).manifest_differs
    changed_comparison = compare_output_archives(left, changed)
    assert changed_comparison.manifest_differs
    assert changed_comparison.failure_lines() == ["manifest.json differs"]
    assert compare_output_archives(left, unparseable).manifest_differs


def test_dbf_differences_report_record_count(tmp_path: Path) -> None:
    left = _archive(
        tmp_path, "left.zip", {"manifest.json": MANIFEST, "data.dbf": _dbf_bytes(3)}
    )
    right = _archive(
        tmp_path, "right.zip", {"manifest.json": MANIFEST, "data.dbf": _dbf_bytes(2)}
    )

    comparison = compare_output_archives(left, right)

    assert not comparison.equal
    difference = comparison.differing[0]
    assert difference.name == "data.dbf"
    assert difference.left_sha256 != difference.right_sha256
    assert comparison.failure_lines() == ["data.dbf: record count 3 != 2"]


def test_dbf_differences_report_field_table(tmp_path: Path) -> None:
    left = _archive(
        tmp_path,
        "left.zip",
        {"manifest.json": MANIFEST, "data.dbf": _dbf_bytes(2)},
    )
    right = _archive(
        tmp_path,
        "right.zip",
        {
            "manifest.json": MANIFEST,
            "data.dbf": _dbf_bytes(2, fields=(("depth", "N", 12, 3),)),
        },
    )

    comparison = compare_output_archives(left, right)

    assert comparison.failure_lines() == [
        "data.dbf: field table differs (id,N,10,0 != depth,N,12,3)"
    ]


def test_shp_differences_report_geometry_type(tmp_path: Path) -> None:
    left = _archive(
        tmp_path, "left.zip", {"manifest.json": MANIFEST, "data.shp": _shp_bytes(11)}
    )
    right = _archive(
        tmp_path, "right.zip", {"manifest.json": MANIFEST, "data.shp": _shp_bytes(5)}
    )

    comparison = compare_output_archives(left, right)

    assert comparison.failure_lines() == [
        "data.shp: geometry type PointZ (11) != Polygon (5)"
    ]


def test_unparseable_members_fall_back_to_content_differs(tmp_path: Path) -> None:
    left = _archive(
        tmp_path, "left.zip", {"manifest.json": MANIFEST, "data.dbf": b"not a dbf"}
    )
    right = _archive(
        tmp_path, "right.zip", {"manifest.json": MANIFEST, "data.dbf": b"also not"}
    )

    comparison = compare_output_archives(left, right)

    assert comparison.failure_lines() == ["data.dbf: content differs"]


def test_unsafe_archives_are_rejected(tmp_path: Path) -> None:
    escaping = _archive(
        tmp_path, "escape.zip", {"manifest.json": MANIFEST, "../evil.txt": b"x"}
    )
    duplicated = _archive(tmp_path, "dupe.zip", {"manifest.json": MANIFEST})
    with zipfile.ZipFile(duplicated, "a") as output:
        output.writestr(zipfile.ZipInfo("manifest.json", (1980, 1, 1, 0, 0, 0)), MANIFEST)
    manifest_free = _archive(tmp_path, "bare.zip", {"data.txt": b"x"})

    for archive in (escaping, duplicated, manifest_free):
        with pytest.raises(ValueError, match="unsafe or invalid plugin ZIP"):
            compare_output_archives(archive, archive)


def test_extract_archive_flat_writes_members_flat(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path, "out.zip", {"manifest.json": MANIFEST, "data.dbf": _dbf_bytes(1)}
    )
    destination = tmp_path / "extracted"

    destination.mkdir()
    names = extract_archive_flat(archive, destination)

    assert names == {"manifest.json", "data.dbf"}
    assert (destination / "manifest.json").read_bytes() == MANIFEST


def test_nc_wrapper_delegates_structural_layers_to_the_platform(tmp_path: Path) -> None:
    """The NC wrapper keeps its CLI contract while the platform does the parsing."""
    members = {"manifest.json": MANIFEST, "data.dbf": _dbf_bytes(2)}
    left = _archive(tmp_path, "left.zip", members)
    right = _archive(tmp_path, "right.zip", members)
    other_fields = _archive(
        tmp_path,
        "fields.zip",
        {"manifest.json": MANIFEST, "data.dbf": _dbf_bytes(2, fields=(("d", "N", 4, 2),))},
    )
    renamed = _archive(tmp_path, "renamed.zip", {"manifest.json": MANIFEST})
    changed_manifest = _archive(
        tmp_path,
        "manifest.zip",
        {"manifest.json": b'{"schema_version": "2.0"}\n', "data.dbf": _dbf_bytes(2)},
    )

    assert nc_compare_archives(left, right) == []
    # Semantic equality is a .shp-level judgement: a lone .dbf byte difference
    # adds no per-feature detail, exactly like the pre-platform wrapper.
    assert nc_compare_archives(left, other_fields) == []
    assert nc_compare_archives(left, changed_manifest) == ["manifest.json differs"]
    assert nc_compare_archives(left, renamed) == [
        f"component sets differ: {['data.dbf', 'manifest.json']} != {['manifest.json']}"
    ]
