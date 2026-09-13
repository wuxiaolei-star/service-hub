from __future__ import annotations

from pathlib import Path

import yaml


def test_compose_has_backend_and_web_services() -> None:
    """Production Compose must pair the backend with the Web console image."""
    model = yaml.safe_load(Path("compose.yaml").read_text("utf-8"))
    assert set(model["services"]) == {"service-hub", "service-hub-web"}

    backend = model["services"]["service-hub"]
    required_data_directory = "${HUB_HOST_DATA_DIR:?HUB_HOST_DATA_DIR must be set}"
    assert backend["image"] == "python-service-hub:1.0.0-linux-amd64"
    assert backend["platform"] == "linux/amd64"
    assert backend["restart"] == "unless-stopped"
    assert backend["ports"] == ["127.0.0.1:8000:8000"]
    assert backend["environment"] == {"HUB_HOST_DATA_DIR": required_data_directory}
    assert backend["volumes"] == [
        f"{required_data_directory}:/data",
        "/var/run/docker.sock:/var/run/docker.sock",
    ]

    web = model["services"]["service-hub-web"]
    assert web["image"] == "python-service-hub-web:1.0.0-linux-amd64"
    assert web["platform"] == "linux/amd64"
    assert web["restart"] == "unless-stopped"
    assert web["ports"] == ["127.0.0.1:8080:8080"]
    assert not web.get("volumes")
    assert web["depends_on"] == {"service-hub": {"condition": "service_healthy"}}


def test_web_dockerfile_is_multi_stage() -> None:
    """The Web image builds the SPA with Node and ships only static assets."""
    dockerfile = Path("web/Dockerfile").read_text("utf-8")
    assert "FROM node:22-alpine" in dockerfile
    assert "npm ci" in dockerfile
    assert "npm run build" in dockerfile
    assert "FROM nginx" in dockerfile
    assert "alpine" in dockerfile
    assert "COPY --from=build" in dockerfile
    assert "8080" in dockerfile
    assert "HEALTHCHECK" in dockerfile


def test_web_dockerignore_keeps_build_context_small() -> None:
    """Dependencies must install inside the image, not leak from the host."""
    ignore = Path("web/.dockerignore").read_text("utf-8")
    assert "node_modules" in ignore
    assert "dist" in ignore


def test_nginx_config_routes_safely() -> None:
    """Nginx must serve the SPA, proxy only /api/v1, and deny /internal/v1."""
    config = Path("web/nginx.conf").read_text("utf-8")
    assert "listen 8080" in config
    assert "location /api/v1/" in config
    assert "proxy_pass http://service-hub:8000/api/v1/" in config
    assert "location /internal/v1/" in config
    assert "return 404" in config
    assert "try_files $uri $uri/ /index.html" in config
    assert "client_max_body_size 10g" in config
    assert "proxy_read_timeout 3700s" in config
    assert "proxy_send_timeout 3700s" in config
    assert "location = /web-health" in config
