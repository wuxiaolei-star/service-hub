from __future__ import annotations

from pathlib import Path


def test_runtime_image_contains_all_components() -> None:
    """The production image must contain every Service Hub runtime component."""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "packages/hub-contracts" in dockerfile
    assert "packages/hub-sdk" in dockerfile
    assert "packages/hub-runner" in dockerfile
    assert "packages/hub-server" in dockerfile
    assert "supervisord.conf" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert 'ENTRYPOINT ["/usr/bin/tini"' in dockerfile


def test_runtime_image_keeps_the_docker_job_data_group_compatible() -> None:
    """Docker job containers running as 65532 must retain group write access to /data."""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "groupadd --system --gid 65532 hub-data" in dockerfile


def test_supervisor_runs_six_managed_processes() -> None:
    """Each persistent process has a distinct, explicit least-privilege user."""
    config = Path("deploy/service_hub/supervisord.conf").read_text(encoding="utf-8")

    assert "[program:hub-api]" in config
    assert "[program:conda-runner]" in config
    assert "[program:docker-runner]" in config
    assert "[program:cleaner]" in config
    assert "[program:scheduler]" in config
    assert "[program:service-manager]" in config
    assert "user=hub-api" in config
    assert "user=conda-runner" in config
    assert "user=docker-runner" in config
    assert config.count("umask=0002") == 6
    assert config.count("autorestart=true") == 6
    assert config.count("stopasgroup=true") == 6
    assert config.count("killasgroup=true") == 6
