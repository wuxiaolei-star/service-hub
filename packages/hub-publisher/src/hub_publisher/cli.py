"""Command line interface for generic plugin publishing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .archive import sha256_file
from .builder import build_plugin
from .project import PluginProject


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hub-plugin")
    subcommands = parser.add_subparsers(dest="command", required=True)
    build = subcommands.add_parser("build", help="build a plugin package")
    build.add_argument("project", type=Path)
    build.add_argument("--runtime", required=True, choices=("conda-pack", "docker"))
    build.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    build.add_argument("--output", required=True, type=Path)
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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
