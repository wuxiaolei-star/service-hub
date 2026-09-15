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


WEB_GUIDE = Path("docs/guides/Web管理台构建部署与使用.md")


def test_readme_names_both_images_and_web_port() -> None:
    """The README entry must teach the backend/web pair and the 8080 console port."""
    readme = README.read_text("utf-8")
    assert "python-service-hub-web:1.0.0-linux-amd64" in readme
    assert "127.0.0.1:8080" in readme
    assert "[Web管理台构建部署与使用]" in readme


def test_web_guide_documents_unified_frontend_backend_workflow() -> None:
    """The Web guide must cover dual-image build/import, Compose, and the console flow."""
    guide = WEB_GUIDE.read_text("utf-8")
    assert "python-service-hub:1.0.0-linux-amd64" in guide
    assert "python-service-hub-web:1.0.0-linux-amd64" in guide
    assert "HUB_HOST_DATA_DIR" in guide
    assert "127.0.0.1:8080" in guide
    assert "127.0.0.1:8000" in guide
    assert "docker compose up -d" in guide
    assert "docker save" in guide
    assert "docker load" in guide
    assert "/internal/v1" in guide
    assert "docker compose config --services" in guide
    assert "docker compose restart" in guide
    assert "/api/v1" in guide


def test_web_guide_covers_plugin_file_and_job_workflow() -> None:
    """Operators must be able to install plugins, upload files, and run Jobs."""
    guide = WEB_GUIDE.read_text("utf-8")
    assert "hubctl plugin install" in guide
    assert "hubctl job run" in guide
    assert "Web" in guide
    assert "新建任务" in guide
    assert "插件" in guide
    assert "文件" in guide
    assert "任务" in guide
    assert "日志" in guide
    assert "取消" in guide
    assert "下载" in guide


def test_single_container_guide_cross_links_web_guide() -> None:
    """The backend guide must point console operators at the Web guide."""
    guide = SINGLE_CONTAINER_GUIDE.read_text("utf-8")
    assert "[Web管理台构建部署与使用]" in guide
    assert "service-hub-web" in guide


V3_SERVICE_DOC = Path("docs/service-docs/服务说明-V3.0.md")
V3_ACCEPTANCE_CHECKLIST = Path("docs/service-docs/V3.0-部署验收清单.md")


def test_readme_links_the_v3_long_running_service_documentation() -> None:
    """The README must route console operators to the V3 service docs and the page."""
    readme = README.read_text("utf-8")

    assert "[服务说明-V3.0]" in readme
    assert "[V3.0-部署验收清单]" in readme
    assert "/services" in readme
    assert "service-manager" in readme


def test_v3_documents_cover_the_operational_flow_and_the_socket_boundary() -> None:
    """An operator must find the deploy/port/log/stop/delete flow and the isolation rule."""
    service_doc = V3_SERVICE_DOC.read_text("utf-8")
    checklist = V3_ACCEPTANCE_CHECKLIST.read_text("utf-8")

    assert "/api/v1/services" in service_doc
    assert "service-manager" in service_doc
    assert "hub-svc-<name>" in service_doc
    assert "V3.0-部署验收清单" in service_doc

    for keyword in (
        "nginxdemos/hello",
        "127.0.0.1",
        "/api/v1/services",
        "service-manager 不可用",
        "hub-svc-",
        "失败排查",
        "服务说明-V3.0",
        "单容器部署与脚本插件使用",
    ):
        assert keyword in checklist, keyword
