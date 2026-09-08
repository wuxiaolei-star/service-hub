# Python Service Hub

Python Service Hub V1 是一个离线、单机的 Python 插件调度服务。Hub 负责插件 Build 注册、
文件、Job、日志和输出；插件发布者负责提前构建目标平台依赖。Hub 不执行 `pip install`、
`conda install` 或 GDAL/PROJ 编译。

V1 支持两种显式运行时：

- `conda-pack`：Runner 在隔离的预构建 Conda 环境中启动一次性进程；
- `docker`：Runner 通过 Docker Socket 启动一次性、无网络、非 root 插件容器。

插件不是常驻 HTTP 服务。每次 `POST /api/v1/jobs` 只启动一次运行，结束后由 Hub 保存结果。

## 架构

```text
业务系统 → Nginx → 127.0.0.1:8000 → hub
                                      ├─ hub-conda-runner（无 Docker Socket）
                                      └─ hub-docker-runner（唯一挂载 Docker Socket）
```

只有 `hub` 发布宿主机回环端口；三个服务共享同一 `/data`。项目主要目录：

```text
packages/hub-contracts       协议与 Pydantic 契约
packages/hub-sdk             插件 SDK
packages/hub-runner          Conda/Docker 执行器
packages/hub-server          FastAPI、SQLite、文件和生命周期 API
packages/nc-to-shp-plugin    NC 转 Shapefile 示例及两种构建脚本
deploy/runner                两个 Runner 镜像
deploy/nginx                 Nginx 边界配置
```

## Linux AMD64 快速部署

仓库应位于默认目录 `/srv/python-service-hub`。若使用其他目录，`HUB_DATA_DIR` 必须设置为
宿主机上的绝对路径，不能使用 `./data`：Docker Runner 会把这个值交给宿主 Docker daemon
作为插件容器的 bind source。

```bash
sudo git clone <approved-repository-url> /srv/python-service-hub
cd /srv/python-service-hub
sudo install -d -m 0750 /srv/python-service-hub/config /srv/python-service-hub/data
sudo cp config/hub.yaml.example config/hub.yaml

RUNNER_TOKEN="$(openssl rand -hex 32)"
sudo sed -i "s/replace-with-a-random-high-entropy-token/${RUNNER_TOKEN}/g" \
  config/hub.yaml compose.yaml
unset RUNNER_TOKEN

sudo docker compose up -d --build
curl -fsS http://127.0.0.1:8000/api/v1/system/health
```

期望健康响应为 `{"status":"UP"}`。随后按完整手册构建并注册：

```text
nc_to_shp-1.0.0-linux-amd64-conda.pypkg
nc_to_shp-1.0.0-linux-amd64-docker.pypkg
```

完整的构建、离线搬运、API 命令、验收、备份和 ARM64 重建流程见
[双运行时插件构建与部署指南](docs/guides/双运行时插件构建与部署.md)。

## 本地质量检查

```bash
python -m pytest
python -m ruff check .
python -m mypy packages
docker compose config
```

真实双运行时验收只在原生 Linux AMD64 Docker 主机执行，并要求外部真实 NC 文件：

```bash
HUB_NC_SAMPLE_PATH=/absolute/path/20260828213102_生成hdf5结果详情信息.nc \
HUB_PLUGIN_PACKAGE_DIR=/srv/python-service-hub/packages/nc-to-shp-plugin/dist \
python -m pytest -m integration tests/integration/test_dual_runtime_nc_to_shp.py -v
```

V1 没有应用层认证。必须保持 Hub 只监听 `127.0.0.1`，并由 Nginx TLS、来源网段限制和
主机防火墙保护；禁止外部访问 `/internal/v1`。
