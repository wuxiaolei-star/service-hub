"""Plugin project discovery and validation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from python_hub_contracts import PluginManifest, load_plugin_manifest

RuntimeType = Literal["conda-pack", "docker"]
Architecture = Literal["amd64", "arm64"]


@dataclass(frozen=True, slots=True)
class PluginProject:
    """A validated source tree ready to be built for a target runtime."""

    root: Path
    manifest_path: Path
    manifest: PluginManifest
    source_dir: Path
    conda_environment: Path | None
    dockerfile: Path | None

    @classmethod
    def load(cls, path: Path) -> PluginProject:
        """Load a plugin project rooted at ``path``."""
        root = path.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("plugin project must be a directory")
        manifest_path = root / "plugin.yaml"
        if not manifest_path.is_file():
            raise ValueError("plugin project requires plugin.yaml")
        if manifest_path.is_symlink():
            raise ValueError("plugin.yaml must not be a symbolic link")
        source_dir = root / "src"
        if not source_dir.is_dir():
            raise ValueError("plugin project requires a real src directory")
        _reject_symlinks(source_dir)
        return cls(
            root=root,
            manifest_path=manifest_path,
            manifest=load_plugin_manifest(manifest_path),
            source_dir=source_dir,
            conda_environment=_first_file(
                root / "environment.yml",
                root / "conda" / "environment.yml",
            ),
            dockerfile=_first_file(root / "Dockerfile", root / "docker" / "Dockerfile"),
        )

    def validate_runtime(self, runtime_type: str) -> Path:
        """Return the selected runtime config file or raise a clear validation error."""
        selected = validate_runtime_type(runtime_type)
        if selected == "conda-pack":
            if self.conda_environment is None:
                raise ValueError("conda-pack runtime requires environment.yml")
            return self.conda_environment
        if self.dockerfile is None:
            raise ValueError("docker runtime requires Dockerfile")
        return self.dockerfile

    def source_digest_root(self) -> Path:
        """Return the package directory that anchors the source digest basis.

        The digest basis is the top-level package named by the entrypoint module
        (e.g. ``nc_to_shp_plugin.main`` -> ``src/nc_to_shp_plugin``). Keeping this
        basis identical to the legacy NC script means rebuilding an already
        registered plugin reproduces its original ``source_sha256`` instead of
        forking the version metadata.
        """
        top_level_package = self.manifest.entrypoint.module.split(".")[0]
        root = self.source_dir / top_level_package
        if not root.is_dir():
            raise ValueError(
                f"entrypoint module requires the src/{top_level_package} package directory"
            )
        return root

    def source_digest(self) -> str:
        """Return the deterministic SHA256 of the packaged plugin sources."""
        root = self.source_digest_root()
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        return digest.hexdigest()

    def source_files(self) -> list[Path]:
        """Return deterministic regular source files."""
        _reject_symlinks(self.source_dir)
        return [
            path
            for path in sorted(self.source_dir.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        ]


def validate_runtime_type(value: str) -> RuntimeType:
    if value not in {"conda-pack", "docker"}:
        raise ValueError("runtime_type must be conda-pack or docker")
    return cast(RuntimeType, value)


def validate_architecture(value: str) -> Architecture:
    if value not in {"amd64", "arm64"}:
        raise ValueError("arch must be amd64 or arm64")
    return cast(Architecture, value)


def _first_file(*paths: Path) -> Path | None:
    for path in paths:
        if path.is_file():
            if path.is_symlink():
                raise ValueError(f"{path.name} must not be a symbolic link")
            return path
    return None


def _reject_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise ValueError(f"{root} must not be a symbolic link")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"plugin source contains symbolic link: {path}")
