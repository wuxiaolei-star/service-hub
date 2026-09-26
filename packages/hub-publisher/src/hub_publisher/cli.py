"""Command line interface for generic plugin publishing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .archive import sha256_file
from .builder import build_plugin
from .dev import run_dev
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
    dev = subcommands.add_parser(
        "dev",
        help="run a plugin project locally against a synthesized job (no Hub required)",
    )
    dev.add_argument("project", type=Path)
    dev.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="NAME=FILE",
        help="job input file; repeatable; NAME is matched from the manifest when omitted",
    )
    dev.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="plugin parameter; repeatable; coerced to the manifest parameter type",
    )
    dev.add_argument(
        "--mode",
        choices=("python", "docker"),
        help="execution mode: local interpreter or docker job container (default: python)",
    )
    dev.add_argument(
        "--keep",
        action="store_true",
        help="keep the temporary job workspace instead of deleting it",
    )
    keygen = subcommands.add_parser(
        "keygen",
        help="generate an Ed25519 key pair for package signing (B9)",
    )
    keygen.add_argument(
        "--out-prefix",
        type=Path,
        default=Path("hub-plugin-key"),
        help="prefix for the <prefix>.sec / <prefix>.pub files (default: ./hub-plugin-key)",
    )
    sign = subcommands.add_parser(
        "sign",
        help="sign a built .pypkg so the Hub can verify it (B9)",
    )
    sign.add_argument("package", type=Path)
    sign.add_argument(
        "--key",
        type=Path,
        default=Path("hub-plugin-key.sec"),
        help="private key file produced by keygen (default: ./hub-plugin-key.sec)",
    )
    sign.add_argument(
        "--out",
        type=Path,
        help="signature output path (default: <package>.sig)",
    )
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
    if args.command == "dev":
        return run_dev(
            project_dir=args.project,
            inputs=args.input,
            params=args.param,
            mode=args.mode,
            keep=args.keep,
        )
    if args.command == "keygen":
        return _keygen(args.out_prefix)
    if args.command == "sign":
        return _sign(args.package, args.key, args.out)
    return 1


def _keygen(out_prefix: Path) -> int:
    from .signing import write_keypair

    try:
        secret_path, public_path, fingerprint = write_keypair(out_prefix)
    except Exception as error:  # missing dependency, refused overwrite, IO problems
        print(f"hub-plugin: {error}", file=sys.stderr)
        return 1
    print(f"secret key: {secret_path} (fingerprint {fingerprint})")
    print("  keep this file offline and out of source control; it can sign accepted packages")
    print(f"public key: {public_path}")
    print("  register its base64 line under plugins.signature.public_keys in the Hub config")
    return 0


def _sign(package: Path, key: Path, out: Path | None) -> int:
    from .signing import sign_package

    try:
        destination, fingerprint = sign_package(package, key, out)
    except Exception as error:  # missing dependency, bad key file, IO problems
        print(f"hub-plugin: {error}", file=sys.stderr)
        return 1
    print(f"signature: {destination} (key fingerprint {fingerprint})")
    print("  upload the package and the signature together: multipart fields 'file' + 'signature'")
    return 0


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
