"""Structured comparison of plugin output archives (plugin conformance toolkit).

The comparator answers "did two runs of the same plugin produce the same
outputs?" with a pure-stdlib core, so the conformance harness can run in any
environment without extra dependencies:

- ZIP safety checks and flat extraction (also reused by the NC plugin's CLI
  comparison tool, which layers its OGR-based semantic pass on top);
- file-name set differences, per-file SHA-256 equality and ``manifest.json``
  equality (the plugin-output contract requires that member);
- when two same-named files differ, a summary of *why* parsed without third
  party dependencies: the dBASE record count and field table for ``.dbf`` and
  the shapefile geometry type for ``.shp``.

Level of strictness: this is a byte/structure-level comparison. Two archives
whose ``.shp`` members merely differ in writer-specific bytes while being
semantically equal (same features, CRS and schema) are reported as different
here; the semantic judgement belongs to a plugin-specific pass (see
``packages/nc-to-shp-plugin/scripts/compare_shapefile_zip.py``).
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

_DBF_HEADER_SIZE = 32
_DBF_FIELD_DESCRIPTOR_SIZE = 32
_DBF_FIELD_TERMINATOR = 0x0D
_SHP_HEADER_SIZE = 100
_SHAPE_TYPE_OFFSET = 32
_SHAPE_TYPES = {
    0: "Null Shape",
    1: "Point",
    3: "PolyLine",
    5: "Polygon",
    8: "MultiPoint",
    11: "PointZ",
    13: "PolyLineZ",
    15: "PolygonZ",
    18: "MultiPointZ",
    21: "PointM",
    23: "PolyLineM",
    25: "PolygonM",
    28: "MultiPointM",
}


@dataclass(frozen=True, slots=True)
class FileDifference:
    """One archive member whose bytes differ between the two archives."""

    name: str
    left_sha256: str
    right_sha256: str
    #: Human-readable difference lines, each prefixed with the member name.
    summary: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArchiveComparison:
    """Structured result of comparing two plugin output ZIP archives."""

    left: Path
    right: Path
    left_names: tuple[str, ...]
    right_names: tuple[str, ...]
    #: ``manifest.json`` content differs (reported as one line, not a member diff).
    manifest_differs: bool
    differing: tuple[FileDifference, ...]

    @property
    def only_in_left(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.left_names) - set(self.right_names)))

    @property
    def only_in_right(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.right_names) - set(self.left_names)))

    @property
    def equal(self) -> bool:
        """True when name sets, manifest and every member's bytes agree."""
        return not (
            self.only_in_left or self.only_in_right or self.manifest_differs or self.differing
        )

    def failure_lines(self) -> list[str]:
        """Flat human-readable difference lines; empty means equal.

        Mirrors the legacy NC comparison output so plugin-local wrappers can
        forward it verbatim: a component-set mismatch short-circuits to one
        line, then the manifest line, then per-member summaries.
        """
        if self.only_in_left or self.only_in_right:
            return [
                f"component sets differ: {sorted(self.left_names)}"
                f" != {sorted(self.right_names)}"
            ]
        lines = ["manifest.json differs"] if self.manifest_differs else []
        for difference in self.differing:
            lines.extend(difference.summary)
        return lines


@dataclass(frozen=True, slots=True)
class DbfSummary:
    """The dBASE III header facts readable without any third-party dependency."""

    record_count: int
    fields: tuple[tuple[str, str, int, int], ...]

    @classmethod
    def parse(cls, data: bytes) -> DbfSummary | None:
        """Parse a ``.dbf`` header; ``None`` when the bytes are not parseable."""
        try:
            if len(data) < _DBF_HEADER_SIZE + 1:
                return None
            record_count = int.from_bytes(data[4:8], "little")
            header_length = int.from_bytes(data[8:10], "little")
            if header_length < _DBF_HEADER_SIZE + 1 or len(data) < header_length:
                return None
            fields: list[tuple[str, str, int, int]] = []
            offset = _DBF_HEADER_SIZE
            while offset < header_length - 1 and data[offset] != _DBF_FIELD_TERMINATOR:
                descriptor = data[offset : offset + _DBF_FIELD_DESCRIPTOR_SIZE]
                if len(descriptor) < _DBF_FIELD_DESCRIPTOR_SIZE:
                    return None
                name = descriptor[:11].split(b"\x00")[0].decode("latin-1", "replace")
                fields.append((name, chr(descriptor[11]), descriptor[16], descriptor[17]))
                offset += _DBF_FIELD_DESCRIPTOR_SIZE
        except (IndexError, ValueError):
            return None
        return cls(record_count=record_count, fields=tuple(fields))


def compare_output_archives(left: Path, right: Path) -> ArchiveComparison:
    """Compare two plugin output ZIP archives: names, manifest and per-file bytes.

    Raises ``ValueError`` for archives that violate the plugin-output ZIP
    contract (unsafe member names, duplicate members or a missing
    ``manifest.json``).
    """
    with zipfile.ZipFile(left) as left_zip, zipfile.ZipFile(right) as right_zip:
        left_names = _validated_names(left_zip, left)
        right_names = _validated_names(right_zip, right)
        manifest_differs = _manifest_differs(left_zip, right_zip)
        differences = []
        for name in sorted(left_names & right_names):
            if name == "manifest.json":
                # Reported once via manifest_differs, not as a member difference.
                continue
            left_bytes = left_zip.read(name)
            right_bytes = right_zip.read(name)
            if left_bytes == right_bytes:
                continue
            differences.append(
                FileDifference(
                    name=name,
                    left_sha256=hashlib.sha256(left_bytes).hexdigest(),
                    right_sha256=hashlib.sha256(right_bytes).hexdigest(),
                    summary=_difference_summary(name, left_bytes, right_bytes),
                )
            )
    return ArchiveComparison(
        left=left,
        right=right,
        left_names=tuple(sorted(left_names)),
        right_names=tuple(sorted(right_names)),
        manifest_differs=manifest_differs,
        differing=tuple(differences),
    )


def extract_archive_flat(archive: Path, destination: Path) -> set[str]:
    """Validate one plugin output ZIP and extract it flat into ``destination``.

    The plugin-output ZIP contract: unique member names, flat relative paths and
    a ``manifest.json`` member. Raises ``ValueError`` when the archive violates
    it, so a hostile archive cannot escape the destination directory.
    """
    with zipfile.ZipFile(archive) as source:
        names = _validated_names(source, archive)
        for name in sorted(names):
            (destination / name).write_bytes(source.read(name))
    return names


def _validated_names(source: zipfile.ZipFile, archive: Path) -> set[str]:
    names = source.namelist()
    if (
        len(names) != len(set(names))
        or "manifest.json" not in names
        or any(not name or name != Path(name).name for name in names)
    ):
        raise ValueError(f"unsafe or invalid plugin ZIP: {archive}")
    return set(names)


def _manifest_differs(left_zip: zipfile.ZipFile, right_zip: zipfile.ZipFile) -> bool:
    """Compare manifests as parsed JSON; unparseable manifests fall back to bytes."""
    left_bytes = left_zip.read("manifest.json")
    right_bytes = right_zip.read("manifest.json")
    if left_bytes == right_bytes:
        return False
    try:
        left_manifest: object = json.loads(left_bytes)
        right_manifest: object = json.loads(right_bytes)
    except (ValueError, UnicodeDecodeError):
        return True
    return left_manifest != right_manifest


def _difference_summary(name: str, left: bytes, right: bytes) -> tuple[str, ...]:
    """Explain a byte difference with the format facts stdlib can parse."""
    if name.endswith(".dbf"):
        lines = _dbf_difference_lines(name, left, right)
    elif name.endswith(".shp"):
        lines = _shp_difference_lines(name, left, right)
    else:
        lines = []
    return tuple(lines) or (f"{name}: content differs",)


def _dbf_difference_lines(name: str, left: bytes, right: bytes) -> list[str]:
    left_summary = DbfSummary.parse(left)
    right_summary = DbfSummary.parse(right)
    if left_summary is None or right_summary is None:
        return []
    lines = []
    if left_summary.record_count != right_summary.record_count:
        lines.append(
            f"{name}: record count {left_summary.record_count}"
            f" != {right_summary.record_count}"
        )
    if left_summary.fields != right_summary.fields:
        lines.append(
            f"{name}: field table differs ({_format_fields(left_summary.fields)}"
            f" != {_format_fields(right_summary.fields)})"
        )
    return lines


def _shp_difference_lines(name: str, left: bytes, right: bytes) -> list[str]:
    left_type = _shape_type(left)
    right_type = _shape_type(right)
    if left_type is None or right_type is None or left_type == right_type:
        return []
    return [
        f"{name}: geometry type {_shape_label(left_type)} != {_shape_label(right_type)}"
    ]


def _shape_type(data: bytes) -> int | None:
    if len(data) < _SHP_HEADER_SIZE:
        return None
    return int.from_bytes(data[_SHAPE_TYPE_OFFSET : _SHAPE_TYPE_OFFSET + 4], "little")


def _shape_label(shape_type: int) -> str:
    return f"{_SHAPE_TYPES.get(shape_type, 'Unknown')} ({shape_type})"


def _format_fields(fields: tuple[tuple[str, str, int, int], ...]) -> str:
    rendered = (f"{name},{type_},{length},{decimals}" for name, type_, length, decimals in fields)
    return ";".join(rendered)
