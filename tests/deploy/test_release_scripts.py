from pathlib import Path

RELEASE_ROOT = Path("deploy/release")
SCRIPT_NAMES = {
    "build-release.sh",
    "install.sh",
    "start.sh",
    "stop.sh",
    "status.sh",
}


def _read_script(name: str) -> str:
    return (RELEASE_ROOT / name).read_text(encoding="utf-8")


def test_release_scripts_are_safe_and_idempotent() -> None:
    names = {path.name for path in RELEASE_ROOT.iterdir()}

    assert names >= SCRIPT_NAMES
    for name in SCRIPT_NAMES:
        source = _read_script(name)
        assert "rm -rf" not in source
        assert "docker compose down -v" not in source
        assert "docker-compose down -v" not in source
        assert "chmod 666" not in source


def test_release_shell_scripts_are_checked_out_with_lf_endings() -> None:
    attributes = Path(".gitattributes").read_text(encoding="utf-8")

    assert "deploy/release/*.sh text eol=lf" in attributes


def test_build_release_creates_amd64_offline_bundle_with_hubctl() -> None:
    source = _read_script("build-release.sh")

    assert "python-service-hub:1.0.0-linux-amd64" in source
    assert "dist/service-hub-1.0.0-linux-amd64.tar.gz" in source
    assert "mktemp -d" in source
    assert "trap" in source
    assert "uname -m" in source
    assert "docker build" in source
    assert "docker save" in source
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
    assert "install -d -m 0750" in source
    assert "HUB_HOST_DATA_DIR" in source
    assert 'if [ ! -f ".env" ]' in source
    assert "/srv/service-hub-data" in source


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
    assert "docker compose down" not in stop
    assert "docker compose down" not in start
    assert "docker compose down" not in status
