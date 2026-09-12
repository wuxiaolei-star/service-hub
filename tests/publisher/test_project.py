from __future__ import annotations

from pathlib import Path

import pytest
from hub_publisher.project import PluginProject

VALID_MANIFEST = """\
spec_version: "1.0"
plugin:
  id: sample_plugin
  name: Sample Plugin
  version: "1.2.3"
sdk:
  version: "1.0"
runtime:
  type: process
  python:
    version: "3.12"
entrypoint:
  module: sample_plugin.main
  function: run
parameters: []
inputs: []
outputs: []
execution:
  timeout: 60
  concurrency: 1
environment_variables:
  required: []
healthcheck:
  enabled: true
  type: import
"""


def _write_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(VALID_MANIFEST, encoding="utf-8")
    package = root / "src" / "sample_plugin"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")


def test_project_requires_manifest_source_and_selected_runtime(tmp_path: Path) -> None:
    """Skipping runtime-file checks would publish packages that builders cannot create."""
    _write_project(tmp_path)

    project = PluginProject.load(tmp_path)

    with pytest.raises(ValueError, match=r"environment\.yml"):
        project.validate_runtime("conda-pack")
    with pytest.raises(ValueError, match="Dockerfile"):
        project.validate_runtime("docker")


def test_project_accepts_root_and_legacy_runtime_config_paths(tmp_path: Path) -> None:
    """Dropping legacy paths would break the existing NC plugin layout."""
    _write_project(tmp_path)
    (tmp_path / "environment.yml").write_text("name: sample\n", encoding="utf-8")
    legacy_docker = tmp_path / "docker" / "Dockerfile"
    legacy_docker.parent.mkdir()
    legacy_docker.write_text("FROM python:3.12-slim\n", encoding="utf-8")

    project = PluginProject.load(tmp_path)

    assert project.validate_runtime("conda-pack") == tmp_path / "environment.yml"
    assert project.validate_runtime("docker") == legacy_docker


def test_project_rejects_symlinks_in_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Following source symlinks could smuggle host files into a plugin package."""
    _write_project(tmp_path)
    target = tmp_path / "outside.txt"
    target.write_text("secret", encoding="utf-8")
    linked = tmp_path / "src" / "sample_plugin" / "outside.txt"
    linked.write_text("placeholder", encoding="utf-8")
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self == linked or original_is_symlink(self),
    )

    with pytest.raises(ValueError, match="symbolic link"):
        PluginProject.load(tmp_path)
