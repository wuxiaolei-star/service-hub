from __future__ import annotations

import io
import sys
from collections.abc import Callable
from pathlib import Path
from typing import IO

import pytest
from hub_publisher.archive import sha256_file
from hub_publisher.builder import CommandResult, build_plugin
from hub_publisher.project import PluginProject

from tests.publisher.test_project import _write_project


class FakeCommandRunner:
    def __init__(self, tmp_path: Path, digest: str = "d" * 64) -> None:
        self.calls: list[tuple[list[str], dict[str, str] | None, bool]] = []
        self.streamed: list[list[str]] = []
        self.tmp_path = tmp_path
        self.digest = digest

    def __call__(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        stdout: Path | None = None,
        capture_stdout: bool = False,
        stream: Callable[[IO[bytes]], None] | None = None,
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
            assert stdout is not None or stream is not None
            self.streamed.append(command)
            if stream is not None:
                stream(io.BytesIO(b"docker image"))
            else:
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


def test_docker_builder_omits_the_apt_mirror_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unset mirror must leave the build arg out, keeping offline builds identical."""
    monkeypatch.delenv("HUB_PLUGIN_APT_MIRROR", raising=False)
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
    assert not any("APT_MIRROR" in part for part in docker_build)


def test_docker_builder_passes_the_apt_mirror_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The apt layer is the other multi-minute download; the mirror has to reach it."""
    monkeypatch.setenv("HUB_PLUGIN_APT_MIRROR", "https://mirrors.example.com")
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
    assert "APT_MIRROR=https://mirrors.example.com" in docker_build


def test_docker_save_streams_straight_into_the_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spooling a ~1GB tar to disk costs a full write plus a full read of it."""
    monkeypatch.delenv("HUB_PLUGIN_APT_MIRROR", raising=False)
    _write_project(tmp_path / "project")
    (tmp_path / "project" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    project = PluginProject.load(tmp_path / "project")
    runner = FakeCommandRunner(tmp_path)

    package = build_plugin(
        project, "docker", "amd64", tmp_path / "out", command_runner=runner, source_date_epoch=0
    )

    assert runner.streamed == [["docker", "image", "save", "sample_plugin:1.2.3-linux-amd64"]]
    assert package.exists()


def _docker_cache_project(tmp_path: Path) -> PluginProject:
    _write_project(tmp_path / "project")
    (tmp_path / "project" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    return PluginProject.load(tmp_path / "project")


def test_docker_build_reuses_the_cached_package_for_unchanged_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rebuilding identical inputs re-runs docker save + zstd for minutes for nothing."""
    for name in ("HUB_PLUGIN_CACHE", "HUB_PLUGIN_CACHE_DIR", "HUB_PLUGIN_ZSTD_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    project = _docker_cache_project(tmp_path)
    output = tmp_path / "out"
    first = build_plugin(
        project, "docker", "amd64", output, command_runner=FakeCommandRunner(tmp_path),
        source_date_epoch=0,
    )

    second_runner = FakeCommandRunner(tmp_path)
    reused = build_plugin(
        project, "docker", "amd64", output, command_runner=second_runner, source_date_epoch=0
    )

    commands = [call[0] for call in second_runner.calls]
    assert not any(command[:2] == ["docker", "build"] for command in commands)
    assert not any(command[:3] == ["docker", "image", "save"] for command in commands)
    assert not any(command[:4] == [sys.executable, "-m", "pip", "wheel"] for command in commands)
    assert reused.name == first.name == "sample_plugin-1.2.3-linux-amd64-docker.pypkg"
    assert sha256_file(reused) == sha256_file(first)
    assert second_runner.streamed == []


def test_changed_plugin_sources_invalidate_the_package_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale cache must never ship sources that no longer match the tree."""
    for name in ("HUB_PLUGIN_CACHE", "HUB_PLUGIN_CACHE_DIR", "HUB_PLUGIN_ZSTD_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    project = _docker_cache_project(tmp_path)
    output = tmp_path / "out"
    build_plugin(
        project, "docker", "amd64", output, command_runner=FakeCommandRunner(tmp_path),
        source_date_epoch=0,
    )

    source = tmp_path / "project" / "src" / "sample_plugin" / "__init__.py"
    source.write_text("CHANGED = True\n", encoding="utf-8")
    second_runner = FakeCommandRunner(tmp_path)
    build_plugin(
        project, "docker", "amd64", output, command_runner=second_runner, source_date_epoch=0
    )

    assert any(call[0][:2] == ["docker", "build"] for call in second_runner.calls)


def test_changed_dockerfile_invalidates_the_package_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editing the Dockerfile changes the built image, so the cache must miss."""
    for name in ("HUB_PLUGIN_CACHE", "HUB_PLUGIN_CACHE_DIR", "HUB_PLUGIN_ZSTD_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    project = _docker_cache_project(tmp_path)
    output = tmp_path / "out"
    build_plugin(
        project, "docker", "amd64", output, command_runner=FakeCommandRunner(tmp_path),
        source_date_epoch=0,
    )

    (tmp_path / "project" / "Dockerfile").write_text(
        "FROM python:3.12-slim\nLABEL changed=1\n", encoding="utf-8"
    )
    second_runner = FakeCommandRunner(tmp_path)
    build_plugin(
        project, "docker", "amd64", output, command_runner=second_runner, source_date_epoch=0
    )

    assert any(call[0][:2] == ["docker", "build"] for call in second_runner.calls)


def test_changed_base_image_invalidates_the_package_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pulled base-image update can change the image even with identical sources."""
    for name in ("HUB_PLUGIN_CACHE", "HUB_PLUGIN_CACHE_DIR", "HUB_PLUGIN_ZSTD_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    project = _docker_cache_project(tmp_path)
    output = tmp_path / "out"
    build_plugin(
        project, "docker", "amd64", output, command_runner=FakeCommandRunner(tmp_path),
        source_date_epoch=0,
    )

    second_runner = FakeCommandRunner(tmp_path, digest="f" * 64)
    build_plugin(
        project, "docker", "amd64", output, command_runner=second_runner, source_date_epoch=0
    )

    assert any(call[0][:2] == ["docker", "build"] for call in second_runner.calls)


def test_tampered_cache_package_falls_back_to_a_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A torn or altered cache file must never be returned as a build result."""
    for name in ("HUB_PLUGIN_CACHE", "HUB_PLUGIN_CACHE_DIR", "HUB_PLUGIN_ZSTD_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    project = _docker_cache_project(tmp_path)
    output = tmp_path / "out"
    build_plugin(
        project, "docker", "amd64", output, command_runner=FakeCommandRunner(tmp_path),
        source_date_epoch=0,
    )
    cache_dir = output / ".pypkg-cache"
    for cached in cache_dir.glob("*.pypkg"):
        cached.write_bytes(b"tampered")

    second_runner = FakeCommandRunner(tmp_path)
    rebuilt = build_plugin(
        project, "docker", "amd64", output, command_runner=second_runner, source_date_epoch=0
    )

    assert any(call[0][:2] == ["docker", "build"] for call in second_runner.calls)
    assert sha256_file(rebuilt) == sha256_file(
        next(path for path in cache_dir.glob("*.pypkg"))
    )


def test_package_cache_can_be_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit opt-out must leave no cache directory behind."""
    monkeypatch.setenv("HUB_PLUGIN_CACHE", "0")
    monkeypatch.delenv("HUB_PLUGIN_CACHE_DIR", raising=False)
    project = _docker_cache_project(tmp_path)
    output = tmp_path / "out"
    build_plugin(
        project, "docker", "amd64", output, command_runner=FakeCommandRunner(tmp_path),
        source_date_epoch=0,
    )

    assert not (output / ".pypkg-cache").exists()
    second_runner = FakeCommandRunner(tmp_path)
    build_plugin(
        project, "docker", "amd64", output, command_runner=second_runner, source_date_epoch=0
    )
    assert any(call[0][:2] == ["docker", "build"] for call in second_runner.calls)


def test_package_cache_keeps_only_the_newest_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ever-growing cache would eat the deployment host's small disk."""
    for name in ("HUB_PLUGIN_CACHE", "HUB_PLUGIN_CACHE_DIR", "HUB_PLUGIN_ZSTD_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    project = _docker_cache_project(tmp_path)
    output = tmp_path / "out"
    source = tmp_path / "project" / "src" / "sample_plugin" / "__init__.py"
    for round_number in range(4):
        source.write_text(f"ROUND = {round_number}\n", encoding="utf-8")
        build_plugin(
            project,
            "docker",
            "amd64",
            output,
            command_runner=FakeCommandRunner(tmp_path),
            source_date_epoch=0,
        )

    assert len(list((output / ".pypkg-cache").glob("*.pypkg"))) == 3


def test_conda_builds_are_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Conda builds depend on host state no local key can pin, so they always run."""
    for name in ("HUB_PLUGIN_CACHE", "HUB_PLUGIN_CACHE_DIR", "HUB_PLUGIN_ZSTD_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    _write_project(tmp_path / "project")
    (tmp_path / "project" / "environment.yml").write_text("name: sample\n", encoding="utf-8")
    project = PluginProject.load(tmp_path / "project")
    monkeypatch.setattr("hub_publisher.builder._host_linux_architecture", lambda: "amd64")
    output = tmp_path / "out"
    build_plugin(
        project, "conda-pack", "amd64", output, command_runner=FakeCommandRunner(tmp_path),
        source_date_epoch=0,
    )

    second_runner = FakeCommandRunner(tmp_path)
    build_plugin(
        project, "conda-pack", "amd64", output, command_runner=second_runner, source_date_epoch=0
    )

    assert any(call[0][:3] == ["conda", "env", "create"] for call in second_runner.calls)
