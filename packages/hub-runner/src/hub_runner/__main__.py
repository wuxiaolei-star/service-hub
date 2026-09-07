from __future__ import annotations

import argparse
from pathlib import Path

from .execution import run_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hub-runner")
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--plugin-root", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    return run_job(
        job_path=args.job,
        manifest_path=args.manifest,
        plugin_root=args.plugin_root,
        result_path=args.result,
    )


if __name__ == "__main__":
    raise SystemExit(main())
