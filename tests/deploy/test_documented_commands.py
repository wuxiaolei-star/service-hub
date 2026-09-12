from __future__ import annotations

from pathlib import Path

README = Path("README.md")
SINGLE_CONTAINER_GUIDE = Path("docs/guides/单容器部署与脚本插件使用.md")
HISTORICAL_GUIDES = [
    Path("docs/guides/Linux-AMD64-首次部署与NC插件验收清单.md"),
    Path("docs/guides/双运行时插件构建与部署.md"),
    Path("docs/guides/项目总览与实施部署.md"),
    Path("docs/guides/构建部署使用与NC插件注册.md"),
]
MIGRATION_HEADING = "## 旧数据迁移、备份与升级"


def test_readme_points_to_single_container_topology() -> None:
    """A README entry that still teaches three services would deploy the old topology."""
    readme = README.read_text("utf-8")
    assert "[单容器部署与脚本插件使用]" in readme
    assert "hub-conda-runner" not in readme
    assert "hub-docker-runner" not in readme
    assert "HUB_HOST_DATA_DIR" in readme
    assert "python-service-hub:1.0.0-linux-amd64" in readme


def test_single_container_guide_documents_primary_workflow() -> None:
    """The primary guide must cover install, start, hubctl, and hub-plugin workflows."""
    guide = SINGLE_CONTAINER_GUIDE.read_text("utf-8")
    assert "python-service-hub:1.0.0-linux-amd64" in guide
    assert "HUB_HOST_DATA_DIR" in guide
    assert "docker compose up -d" in guide
    assert "curl http://127.0.0.1:8000/api/v1/system/health" in guide
    assert "hubctl plugin install" in guide
    assert "hubctl file upload" in guide
    assert "hubctl job run" in guide
    assert "hubctl job download" in guide
    assert "hub-plugin build" in guide
    assert "templates/conda-script-plugin" in guide
    assert "templates/docker-job-plugin" in guide


def test_guide_migration_section_requires_backup_and_forbids_down_v() -> None:
    """Skipping backups or running down -v would destroy plugin data during migration."""
    guide = SINGLE_CONTAINER_GUIDE.read_text("utf-8")
    assert MIGRATION_HEADING in guide
    migration = guide.split(MIGRATION_HEADING, 1)[1]
    assert "备份" in migration
    assert "禁止" in migration
    assert "docker compose down -v" in migration


def test_legacy_guides_are_marked_historical() -> None:
    """Old three-container guides without a banner would remain a deployment entry."""
    for path in HISTORICAL_GUIDES:
        head = "\n".join(path.read_text("utf-8").splitlines()[:10])
        assert "旧拓扑" in head, path
        assert "不再作为部署入口" in head, path
        assert "单容器部署与脚本插件使用" in head, path
