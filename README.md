# Python Service Hub

Python Service Hub V1 是一个离线、单机的 Python 插件调度服务。Hub 负责插件 Build 注册、
文件、Job、日志和输出；插件发布者负责提前构建目标平台依赖。Hub 不执行 `pip install`、
`conda install` 或 GDAL/PROJ 编译。

V1 以**单容器**方式交付：一个 `service-hub` 容器内用 tini + supervisord 运行 Hub API、
conda Runner 和 Docker Runner 三个降权进程，同时支持两种显式运行时：

- `conda-pack`：Runner 在隔离的预构建 Conda 环境中启动一次性进程；
- `docker`：Runner 通过 Docker Socket 启动一次性、无网络、非 root 插件容器。

插件不是常驻 HTTP 服务。每次 `POST /api/v1/jobs` 只启动一次运行，结束后由 Hub 保存结果。

## 架构

```text
业务系统 → Nginx → 127.0.0.1:8000 → service-hub 容器
                                      ├─ hub-api（无 Docker Socket）
                                      ├─ conda-runner（无 Docker Socket）
                                      └─ docker-runner（唯一访问 Docker Socket）
```

只有一个长期运行服务 `service-hub`，发布宿主机回环端口 `127.0.0.1:8000`；宿主机数据
目录 `HUB_HOST_DATA_DIR`（默认 `/srv/service-hub-data`）bind 到容器内 `/data`。
项目主要目录：

```text
packages/hub-contracts       协议与 Pydantic 契约
packages/hub-sdk             插件 SDK
packages/hub-runner          Conda/Docker 执行器
packages/hub-server          FastAPI、SQLite、文件和生命周期 API
packages/hub-publisher       通用 .pypkg 构建库与 hub-plugin 命令
packages/nc-to-shp-plugin    NC 转 Shapefile 示例插件
templates/                   conda/docker 两种最小脚本插件模板
web/                         React 管理台 SPA、Nginx 配置与 Web 镜像
deploy/service_hub           单容器 bootstrap、supervisord、healthcheck
deploy/release               离线发布包制作与安装/启停脚本
deploy/nginx                 Nginx 边界配置
tools/hubctl                 标准库实现的宿主机管理客户端
```

## Linux AMD64 快速部署

唯一必填变量是 `HUB_HOST_DATA_DIR`：宿主机数据绝对路径（至少两级，禁止 `/`、`/srv`、
`/tmp`、`/var`、`/data`）。Docker Runner 会把它交给宿主 Docker daemon 作为插件容器
bind source，因此不能使用 `./data` 等相对路径。

```bash
cd /srv/python-service-hub
sudo install -d -m 0750 /srv/service-hub-data
sudo docker build -t python-service-hub:1.0.0-linux-amd64 .
sudo docker build -t python-service-hub-web:1.0.0-linux-amd64 web
sudo HUB_HOST_DATA_DIR=/srv/service-hub-data docker compose up -d
curl -fsS http://127.0.0.1:8000/api/v1/system/health
```

后端监听 `127.0.0.1:8000`，Web 管理台监听 `127.0.0.1:8080`（浏览器直接访问）。期望
健康响应为 `{"status":"UP"}`。离线交付时改用 `deploy/release/build-release.sh` 制作
发布包（已包含两个镜像），在目标机执行包内 `install.sh` 和 `start.sh`。

部署、运维、hubctl 使用、hub-plugin 构建脚本插件、备份迁移和常见错误的完整手册见
[单容器部署与脚本插件使用](docs/guides/单容器部署与脚本插件使用.md)；Web 管理台的
构建、部署与浏览器操作流程见
[Web管理台构建部署与使用](docs/guides/Web管理台构建部署与使用.md)。

## 插件发布与运行

发布者把插件项目（或复制 `templates/` 下的 conda/docker 模板新建的项目）构建为
`.pypkg` 并注册：

```bash
hub-plugin build packages/nc-to-shp-plugin --runtime conda-pack --arch amd64 \
    --output packages/nc-to-shp-plugin/dist
HUB_URL=http://127.0.0.1:8000 python tools/hubctl plugin install \
    packages/nc-to-shp-plugin/dist/nc_to_shp-1.0.0-linux-amd64-conda.pypkg
HUB_URL=http://127.0.0.1:8000 python tools/hubctl file upload sample.nc
HUB_URL=http://127.0.0.1:8000 python tools/hubctl job run nc_to_shp \
    --version 1.0.0 --runtime conda-pack --input source_nc=file_<id> --params params.json
```

NC 示例插件的固定产物名为
`nc_to_shp-1.0.0-linux-amd64-conda.pypkg` 与
`nc_to_shp-1.0.0-linux-amd64-docker.pypkg`。

## 本地质量检查

```bash
python -m pytest -m "not integration"
python -m ruff check .
python -m mypy packages
HUB_HOST_DATA_DIR=/srv/service-hub-data docker compose config --quiet
```

真实单容器双运行时验收只在原生 Linux AMD64 Docker 主机执行，并要求外部真实 NC 文件：

```bash
HUB_NC_SAMPLE_PATH=/absolute/path/sample.nc \
HUB_PLUGIN_PACKAGE_DIR=/srv/python-service-hub/packages/nc-to-shp-plugin/dist \
python -m pytest -m integration tests/integration/test_dual_runtime_nc_to_shp.py -v
python -m pytest -m integration tests/integration/test_single_container_lifecycle.py -v
```

V1 没有应用层认证。必须保持 Hub 只监听 `127.0.0.1`，并由 Nginx TLS、来源网段限制和
主机防火墙保护；禁止外部访问 `/internal/v1`。容器内只有 docker-runner 进程可以访问
Docker Socket，`hub-api` 与 `conda-runner` 被明确拒绝。
