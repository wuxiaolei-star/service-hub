"""Cleaner process registration and lifecycle tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))

from service_hub import cleaner_service


def test_supervisord_manages_four_processes() -> None:
    """The cleaner joins the three existing supervised processes."""
    config = Path("deploy/service_hub/supervisord.conf").read_text("utf-8")
    assert "[program:cleaner]" in config
    assert "cleaner_service" in config
    assert "user=hub-api" in config
    for name in ("hub-api", "conda-runner", "docker-runner"):
        assert f"[program:{name}]" in config


def test_healthcheck_requires_cleaner_process() -> None:
    source = Path("deploy/service_hub/healthcheck.py").read_text("utf-8")
    assert '"cleaner"' in source


def test_cleaner_once_mode_runs_single_sweep(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """`--once` executes one sweep and exits without looping."""
    import json

    os.environ["HUB_CONFIG_PATH"] = str(tmp_path / "hub.yaml")
    os.environ["HUB_DATA_ROOT"] = str(tmp_path)
    (tmp_path / "hub.yaml").write_text(
        "deployment:\n  mode: offline\n"
        "storage:\n  root: " + tmp_path.as_posix() + "\n"
        "database:\n  url: sqlite:///" + (tmp_path / "hub.db").as_posix() + "\n"
        "uploads:\n  max_size_bytes: 1024\n"
        "runner:\n  shared_token: x\n",
        encoding="utf-8",
    )

    exit_code = cleaner_service.main(once=True)

    assert exit_code == 0
    del json
