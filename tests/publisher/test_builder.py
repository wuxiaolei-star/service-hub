from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hub_publisher.builder import CommandResult, build_plugin
from hub_publisher.project import PluginProject

from tests.publisher.test_project import _write_project


class FakeCommandRunner:
    def __init__(self, tmp_path: Path, digest: str = "d" * 64) -> None:
        self.calls: list[tuple[list[str], dict[str, str] | None, bool]] = []
        self.tmp_path = tmp_path
        self.digest = digest

    def __call__(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        stdout: Path | None = None,
        capture_stdout: bool = False,
    ) -> CommandResult:
        self.calls.append((command, env, capture_stdout))
        if command[:4] == [sys.executable, "-m", "pip", "wheel"]:
            wheel_dir = Path(command[command.index("--wheel-dir") + 1])
            wheel_dir.mkdir(parents=True, exist_ok=True)
            (wheel_dir / f"{Path(command[-1]).name}-0.1.0-py3-none-any.whl").write_bytes(
                b"wheel"
            )
        elif command[:3] == ["conda", "run", "--prefix"]:
            Path(command[command.index("--output") + 1]).write_bytes(b"conda runtime")
        elif command[:4] == ["docker", "image", "inspect", "--format"]:
            return CommandResult(stdout=f"sha256:{self.digest}\n")
        elif command[:3] == ["docker", "image", "save"]:
            assert stdout is not None
            stdout.write_bytes(b"docker image")
        return CommandResult(stdout="")


def test_conda_builder_uses_offline_wheelhouse_and_native_architecture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing offline pip settings would let conda builds depend on the network."""
    _write_project(tmp_path / "project")
    (tmp_path / "project" / "environment.yml").write_text("name: sample\n", encoding="utf-8")
    project = PluginProject.load(tmp_path / "project")
    runner = FakeCommandRunner(tmp_path)
    monkeypatch.setattr("hub_publisher.builder._host_linux_architecture", lambda: "amd64")

    package = build_plugin(
        project,
        "conda-pack",
        "amd64",
        tmp_path / "out",
        command_runner=runner,
        source_date_epoch=0,
    )

    commands = [call[0] for call in runner.calls]
    assert package.name == "sample_plugin-1.2.3-linux-amd64-conda.pypkg"
    assert [command[:4] for command in commands[:3]] == [
        [sys.executable, "-m", "pip", "wheel"],
        [sys.executable, "-m", "pip", "wheel"],
        [sys.executable, "-m", "pip", "wheel"],
    ]
    env_create = next(
        call for call in runner.calls if call[0][:3] == ["conda", "env", "create"]
    )
    assert env_create[1] is not None
    assert env_create[1]["PIP_NO_INDEX"] == "1"
    assert "PIP_FIND_LINKS" in env_create[1]
    assert not any(call[2] for call in runner.calls)
    assert any(command[:3] == ["conda", "run", "--prefix"] for command in commands)


def test_docker_builder_uses_explicit_platform_and_image_save(
    tmp_path: Path,
) -> None:
    """Omitting --platform or digest inspection would make Docker packages ambiguous."""
    _write_project(tmp_path / "project")
    (tmp_path / "project" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    project = PluginProject.load(tmp_path / "project")
    runner = FakeCommandRunner(tmp_path, digest="e" * 64)

    package = build_plugin(
        project,
        "docker",
        "arm64",
        tmp_path / "out",
        command_runner=runner,
        source_date_epoch=0,
    )

    commands = [call[0] for call in runner.calls]
    docker_build = next(command for command in commands if command[:2] == ["docker", "build"])
    assert package.name == "sample_plugin-1.2.3-linux-arm64-docker.pypkg"
    assert docker_build[docker_build.index("--platform") + 1] == "linux/arm64"
    inspect = next(
        call for call in runner.calls if call[0][:4] == ["docker", "image", "inspect", "--format"]
    )
    assert inspect[2]
    assert any(command[:3] == ["docker", "image", "save"] for command in commands)


def test_builder_rejects_unknown_architecture(tmp_path: Path) -> None:
    """Accepting arbitrary architectures would produce packages the Hub cannot install."""
    _write_project(tmp_path / "project")
    project = PluginProject.load(tmp_path / "project")

    with pytest.raises(ValueError, match="amd64 or arm64"):
        build_plugin(project, "docker", "s390x", tmp_path / "out")


def test_docker_builder_omits_the_pip_mirror_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unset mirror must leave the build arg out entirely, not pass an empty one.

    Passing ``--build-arg PIP_INDEX_URL=`` would still be inert, but omitting it keeps
    the command identical to what an offline build has always produced.
    """
    monkeypatch.delenv("HUB_PLUGIN_PIP_INDEX_URL", raising=False)
    _write_project(tmp_path / "project")
    (tmp_path / "project" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    project = PluginProject.load(tmp_path / "project")
    runner = FakeCommandRunner(tmp_path)

    build_plugin(
        project, "docker", "amd64", tmp_path / "out", command_runner=runner, source_date_epoch=0
    )

    docker_build = next(
        command for command, _env, _cap in runner.calls if command[:2] == ["docker", "build"]
    )
    assert not any("PIP_INDEX_URL" in part for part in docker_build)


def test_docker_builder_passes_the_pip_mirror_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured mirror has to reach the Dockerfile, or the build stays slow."""
    monkeypatch.setenv("HUB_PLUGIN_PIP_INDEX_URL", "https://mirrors.example.com/pypi/simple")
    _write_project(tmp_path / "project")
    (tmp_path / "project" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    project = PluginProject.load(tmp_path / "project")
    runner = FakeCommandRunner(tmp_path)

    build_plugin(
        project, "docker", "amd64", tmp_path / "out", command_runner=runner, source_date_epoch=0
    )

    docker_build = next(
        command for command, _env, _cap in runner.calls if command[:2] == ["docker", "build"]
    )
    assert "PIP_INDEX_URL=https://mirrors.example.com/pypi/simple" in docker_build
