"""Backwards-compatible NC plugin wrapper around the generic hub publisher."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parents[1]
PUBLISHER_SRC = REPOSITORY_ROOT / "packages" / "hub-publisher" / "src"
if PUBLISHER_SRC.is_dir() and str(PUBLISHER_SRC) not in sys.path:
    sys.path.insert(0, str(PUBLISHER_SRC))

from hub_publisher.archive import create_plugin_package  # noqa: E402
from hub_publisher.builder import build_plugin, create_build_manifest  # noqa: E402
from hub_publisher.project import (  # noqa: E402
    PluginProject,
    validate_architecture,
    validate_runtime_type,
)


def build_package(
    *,
    runtime_type: str,
    arch: str,
    output_dir: Path,
    prepared_runtime: Path | None = None,
    docker_digest: str | None = None,
    source_date_epoch: int | None = None,
) -> Path:
    """Build or consume a target runtime, then create one immutable plugin package."""
    project = PluginProject.load(PACKAGE_ROOT)
    selected_runtime = validate_runtime_type(runtime_type)
    selected_arch = validate_architecture(arch)
    if prepared_runtime is None:
        if docker_digest is not None:
            raise ValueError("docker_digest is only valid with --prepared-runtime")
        return build_plugin(
            project,
            selected_runtime,
            selected_arch,
            output_dir,
            source_date_epoch=source_date_epoch,
        )

    runtime_archive = prepared_runtime.resolve(strict=True)
    epoch = source_date_epoch
    if epoch is None:
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    build = create_build_manifest(
        project,
        selected_runtime,
        selected_arch,
        runtime_archive,
        docker_digest=docker_digest,
        source_date_epoch=epoch,
    )
    return create_plugin_package(project, build, runtime_archive, output_dir, epoch)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, choices=("conda-pack", "docker"))
    parser.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prepared-runtime", type=Path)
    parser.add_argument("--docker-digest")
    args = parser.parse_args(argv)
    package = build_package(
        runtime_type=args.runtime,
        arch=args.arch,
        output_dir=args.output,
        prepared_runtime=args.prepared_runtime,
        docker_digest=args.docker_digest,
    )
    print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
