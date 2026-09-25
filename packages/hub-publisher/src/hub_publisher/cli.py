"""Command line interface for generic plugin publishing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .archive import sha256_file
from .builder import build_plugin
from .package_check import verify_plugin_package
from .project import PluginProject


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hub-plugin")
    subcommands = parser.add_subparsers(dest="command", required=True)
    build = subcommands.add_parser("build", help="build a plugin package")
    build.add_argument("project", type=Path)
    build.add_argument("--runtime", required=True, choices=("conda-pack", "docker"))
    build.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    build.add_argument("--output", required=True, type=Path)
    validate = subcommands.add_parser(
        "validate", help="validate a plugin project and optionally a built package"
    )
    validate.add_argument("project", type=Path)
    validate.add_argument("--runtime", choices=("conda-pack", "docker"))
    validate.add_argument("--package", type=Path)
    args = parser.parse_args(argv)

    if args.command == "build":
        try:
            package = build_plugin(
                PluginProject.load(args.project),
                args.runtime,
                args.arch,
                args.output,
            )
        except Exception as error:
            print(f"hub-plugin: {error}", file=sys.stderr)
            return 1
        resolved = package.resolve()
        print(f"{resolved} {sha256_file(resolved)}")
        return 0
    if args.command == "validate":
        return _validate(args.project, args.runtime, args.package)
    return 1


def _validate(project: Path, runtime: str | None, package: Path | None) -> int:
    problems: list[str] = []
    loaded: PluginProject | None = None
    try:
        loaded = PluginProject.load(project)
    except Exception as error:
        problems.append(f"project: {error}")
    if loaded is not None and runtime is not None:
        try:
            loaded.validate_runtime(runtime)
        except Exception as error:
            problems.append(f"runtime {runtime}: {error}")
    if package is not None:
        problems.extend(f"package: {item}" for item in verify_plugin_package(package))
    if problems or loaded is None:
        for problem in problems:
            print(f"validate: {problem}", file=sys.stderr)
        return 1
    manifest = loaded.manifest
    print(f"plugin: {manifest.plugin.id} version {manifest.plugin.version}")
    print("project: OK")
    print("manifest: OK")
    if runtime is not None:
        print(f"runtime {runtime}: OK")
    if package is not None:
        print("package: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
