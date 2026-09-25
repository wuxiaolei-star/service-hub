import hashlib
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RELEASE_ROOT = Path("deploy/release")
SCRIPT_NAMES = {
    "build-release.sh",
    "install.sh",
    "start.sh",
    "stop.sh",
    "status.sh",
}

# The scripts under deploy/release are POSIX shell that must be executed to be
# verified. On Windows "bash" resolves to the WSL launcher, so running them from
# a native Windows checkout is not possible; the checks stay enforced on Linux.
POSIX_BASH = pytest.mark.skipif(
    sys.platform == "win32",
    reason="release scripts need a POSIX bash; Windows maps bash to WSL",
)


def _read_script(name: str) -> str:
    return (RELEASE_ROOT / name).read_text(encoding="utf-8")


def _bash_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if drive:
        relative = resolved.as_posix().split(":", 1)[1].lstrip("/")
        return f"/mnt/{drive}/{relative}"
    return resolved.as_posix()


def _prepare_release_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    for name in SCRIPT_NAMES - {"build-release.sh", "stop.sh"}:
        shutil.copy2(RELEASE_ROOT / name, release_dir / name)
    digest = hashlib.sha256(b"fake-image").hexdigest()
    web_digest = hashlib.sha256(b"fake-web-image").hexdigest()
    (release_dir / "service-hub-image.tar").write_bytes(b"fake-image")
    (release_dir / "service-hub-web-image.tar").write_bytes(b"fake-web-image")
    (release_dir / "SHA256SUMS").write_text(
        f"{digest}  service-hub-image.tar\n{web_digest}  service-hub-web-image.tar\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_log_posix = _bash_path(docker_log)
    docker_stub = fake_bin / "docker"
    docker_stub.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> {shlex.quote(docker_log_posix)}
case "$*" in
  "compose version") echo "Docker Compose version test" ;;
  "load -i service-hub-image.tar") echo "Loaded fake image" ;;
  "load -i service-hub-web-image.tar") echo "Loaded fake web image" ;;
  "compose up -d") echo "Started fake service" ;;
  "compose ps") echo "NAME STATUS" ;;
  "compose ps -q service-hub") echo "service-hub-container" ;;
  inspect*) echo "service-hub health=healthy status=running" ;;
  *) echo "unexpected docker call: $*" >&2; exit 64 ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    docker_stub.chmod(0o755)
    install_stub = fake_bin / "install"
    install_stub.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf 'install %s\\n' "$*" >> {shlex.quote(docker_log_posix)}
""",
        encoding="utf-8",
        newline="\n",
    )
    install_stub.chmod(0o755)
    return release_dir, fake_bin, docker_log


def _run_bash(
    release_dir: Path,
    fake_bin: Path,
    command: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assignments = " ".join(
        f"{name}={shlex.quote(value)}" for name, value in (env or {}).items()
    )
    script = (
        f"cd {shlex.quote(_bash_path(release_dir))} && "
        f"PATH={shlex.quote(_bash_path(fake_bin))}:\"$PATH\" "
        f"{assignments} {command}"
    )
    return subprocess.run(
        ["bash", "-lc", script],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
    )


def test_release_scripts_are_safe_and_idempotent() -> None:
    names = {path.name for path in RELEASE_ROOT.iterdir()}

    assert names >= SCRIPT_NAMES
    for name in SCRIPT_NAMES:
        source = _read_script(name)
        assert "rm -rf" not in source
        assert "docker compose down -v" not in source
        assert "docker-compose down -v" not in source
        assert "chmod 666" not in source


def test_everything_linux_executes_is_pinned_to_lf_endings() -> None:
    """core.autocrlf must not be able to write CRLF into a file Linux has to execute.

    A CRLF ``docker-entrypoint.sh`` makes the kernel resolve the shebang as
    ``/bin/sh\\r``, so tini fails the exec with "No such file or directory" and the
    container restart-loops with exit 127 while the image contents look correct.
    The release scripts and tools/hubctl break the same way on any Linux host, so
    the rule has to cover every shell script rather than one directory.
    """
    attributes = Path(".gitattributes").read_text(encoding="utf-8")

    for pattern in ("*.sh", "tools/hubctl", "Dockerfile", "**/Dockerfile"):
        assert re.search(
            rf"^{re.escape(pattern)}\s+text\s+eol=lf\s*$", attributes, re.MULTILINE
        ), f"{pattern} must be pinned to LF endings"


@pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git check-attr is unavailable inside the runtime image used by deploy gate",
)
def test_nested_dockerfiles_are_actually_matched_by_their_gitattributes_rule() -> None:
    """A bare ``Dockerfile`` pattern only covers the repository root.

    The plugin images live at ``packages/*/docker/Dockerfile`` and are built by a Linux
    host, so they need the same LF guarantee. Verified through ``git check-attr`` rather
    than by re-reading ``.gitattributes`` — the point is that the attribute is *applied*
    to the nested path, which a pattern-literal assertion cannot show.
    """
    nested = "packages/nc-to-shp-plugin/docker/Dockerfile"
    result = subprocess.run(
        ["git", "check-attr", "eol", "--", nested],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip().endswith("eol: lf"), result.stdout


def test_files_linux_executes_hold_lf_in_the_working_tree() -> None:
    """The attribute must be backed by real LF bytes, not just a repository rule."""
    names = (
        "docker-entrypoint.sh",
        "tools/hubctl",
        "packages/nc-to-shp-plugin/docker/Dockerfile",
    )
    for name in names:
        payload = Path(name).read_bytes()
        assert b"\r\n" not in payload, f"{name} contains CRLF in the working tree"


def test_build_release_creates_amd64_offline_bundle_with_hubctl() -> None:
    source = _read_script("build-release.sh")

    assert "python-service-hub:1.0.0-linux-amd64" in source
    assert "python-service-hub-web:1.0.0-linux-amd64" in source
    assert "dist/service-hub-1.0.0-linux-amd64.tar.gz" in source
    assert "mktemp -d" in source
    assert "trap" in source
    assert "uname -m" in source
    assert "docker build" in source
    assert "docker save" in source
    assert "service-hub-image.tar" in source
    assert "service-hub-web-image.tar" in source
    assert "sha256sum" in source
    assert "tar -czf" in source
    assert "tools/hubctl" in source
    assert "compose.yaml" in source
    assert "service-hub-linux-amd64" in source


def test_install_script_preserves_existing_env_and_loads_image() -> None:
    source = _read_script("install.sh")

    assert "sha256sum -c SHA256SUMS" in source
    assert "command -v docker" in source
    assert "docker compose version" in source
    assert "command -v python3" in source
    assert "docker load -i service-hub-image.tar" in source
    assert "docker load -i service-hub-web-image.tar" in source
    assert "install -d -m 0750" in source
    assert "HUB_HOST_DATA_DIR" in source
    assert 'if [ ! -f ".env" ]' in source
    assert "/srv/service-hub-data" in source


@POSIX_BASH
def test_install_writes_normalized_absolute_data_dir(tmp_path: Path) -> None:
    release_dir, fake_bin, _docker_log = _prepare_release_fixture(tmp_path)
    raw_data_dir = f"{_bash_path(tmp_path / 'data-parent')}/../normalized-data"
    normalized_data_dir = f"{_bash_path(tmp_path)}/normalized-data"

    result = _run_bash(
        release_dir,
        fake_bin,
        "bash ./install.sh",
        env={"HUB_HOST_DATA_DIR": raw_data_dir},
    )

    assert result.returncode == 0, result.stderr
    assert (release_dir / ".env").read_text(encoding="utf-8") == (
        f"HUB_HOST_DATA_DIR={normalized_data_dir}\n"
    )


@POSIX_BASH
def test_install_rejects_broad_normalized_data_dir_before_installing(
    tmp_path: Path,
) -> None:
    release_dir, fake_bin, docker_log = _prepare_release_fixture(tmp_path)

    result = _run_bash(
        release_dir,
        fake_bin,
        "bash ./install.sh",
        env={"HUB_HOST_DATA_DIR": "/tmp/.."},
    )

    assert result.returncode != 0
    assert "too broad" in result.stderr
    docker_calls = docker_log.read_text(encoding="utf-8")
    assert "load -i service-hub-image.tar" not in docker_calls
    assert "install " not in docker_calls
    assert not (release_dir / ".env").exists()


@POSIX_BASH
def test_start_rejects_existing_env_with_broad_dotdot_path(tmp_path: Path) -> None:
    release_dir, fake_bin, docker_log = _prepare_release_fixture(tmp_path)
    (release_dir / ".env").write_text("HUB_HOST_DATA_DIR=/srv/foo/../..\n", encoding="utf-8")

    result = _run_bash(release_dir, fake_bin, "bash ./start.sh")

    assert result.returncode != 0
    assert "too broad" in result.stderr
    assert not docker_log.exists()


def test_lifecycle_scripts_use_single_service_without_removing_data() -> None:
    start = _read_script("start.sh")
    stop = _read_script("stop.sh")
    status = _read_script("status.sh")

    assert "docker compose up -d" in start
    assert "docker compose stop" in stop
    assert "docker compose ps" in status
    assert "docker inspect" in status
    assert "/api/v1/system/health" in status
    assert "service-hub" in status
    assert "127.0.0.1" in status and "8080" in status
    assert "docker compose down" not in stop
    assert "docker compose down" not in start
    assert "docker compose down" not in status


@POSIX_BASH
def test_status_reports_unavailable_hub_health_without_traceback(tmp_path: Path) -> None:
    release_dir, fake_bin, _docker_log = _prepare_release_fixture(tmp_path)

    result = _run_bash(release_dir, fake_bin, "bash ./status.sh")
    combined_output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "NAME STATUS" in combined_output
    assert "service-hub health=healthy status=running" in combined_output
    assert "hub health unavailable:" in combined_output
    assert "Traceback" not in combined_output
