# Web 管理台构建部署与使用

本指南描述 Service Hub Web 管理台的构建、部署与日常使用。Web 管理台是一个中文界面的
React SPA，由独立 Nginx 镜像 `python-service-hub-web:1.0.0-linux-amd64` 提供服务，
通过 Compose 与单容器后端 `python-service-hub:1.0.0-linux-amd64` 一起交付。

拓扑要点：

- Compose 只有两个服务：`service-hub`（后端）与 `service-hub-web`（管理台）；
- 浏览器访问 `http://127.0.0.1:8080`，由 Nginx 提供 SPA 页面并代理 `/api/v1` 到后端；
- 后端诊断端口保持 `127.0.0.1:8000` 回环监听；
- Nginx 明确拒绝 `/internal/v1/*`（返回 404），内部协议端口不暴露到浏览器；
- Web 容器不挂载 `/data`，也不挂载 Docker Socket；
- 唯一必填部署变量仍是 `HUB_HOST_DATA_DIR`。

## 1. 前置条件

宿主机（Linux AMD64 服务器或本地 Docker Desktop）需要：

- Docker Engine 与 Docker Compose v2；
- 数据目录绝对路径（下文以 `/srv/service-hub-data` 为例）。

## 2. 本地构建双镜像（Windows Docker Desktop 或 Linux）

在仓库根目录执行（Windows 用户在 PowerShell 中同样适用）：

```bash
# 后端镜像
docker build --platform linux/amd64 -t python-service-hub:1.0.0-linux-amd64 .

# Web 管理台镜像（Node 构建 SPA + Nginx 运行）
docker build --platform linux/amd64 -t python-service-hub-web:1.0.0-linux-amd64 web

docker image inspect python-service-hub:1.0.0-linux-amd64
docker image inspect python-service-hub-web:1.0.0-linux-amd64
```

## 3. 导出镜像（离线搬运）或直接启动

方式 A：离线交付——导出双镜像后拷贝到服务器：

```bash
docker save -o service-hub-image.tar python-service-hub:1.0.0-linux-amd64
docker save -o service-hub-web-image.tar python-service-hub-web:1.0.0-linux-amd64
# 连同 compose.yaml 一起拷贝到服务器后：
docker load -i service-hub-image.tar
docker load -i service-hub-web-image.tar
```

方式 B：也可以直接使用发布包
（`deploy/release/build-release.sh` 生成的
`dist/service-hub-1.0.0-linux-amd64.tar.gz` 已包含两个镜像、双服务 Compose 与全部
运维脚本），在服务器解压后执行包内 `install.sh` 与 `start.sh`。

方式 C：本地 Docker Desktop 直接启动验证：

```bash
export HUB_HOST_DATA_DIR=/srv/service-hub-data   # Windows 可改为 D:/service-hub-data
docker compose up -d
```

## 4. 启动与健康检查

```bash
HUB_HOST_DATA_DIR=/srv/service-hub-data docker compose up -d
docker compose config --services   # 应只输出 service-hub 与 service-hub-web
docker compose ps            # 两个服务均应 healthy/running
```

验证四个入口：

```bash
# 1) 后端直连健康
curl http://127.0.0.1:8000/api/v1/system/health        # {"status":"UP"}

# 2) Web 控制台页面
curl -I http://127.0.0.1:8080/                          # 200, text/html

# 3) 经 Nginx 代理的 API
curl http://127.0.0.1:8080/api/v1/system/health        # {"status":"UP"}

# 4) 内部端口必须被拒绝
curl -I http://127.0.0.1:8080/internal/v1/system/health # 404
```

浏览器打开 `http://127.0.0.1:8080` 即可进入管理台。远程服务器可用 SSH 隧道：

```bash
ssh -L 8080:127.0.0.1:8080 user@server
# 然后在本机浏览器打开 http://127.0.0.1:8080
```

## 5. 日常运维

```bash
docker compose ps            # 服务与健康状态
docker compose logs -f service-hub      # 后端三进程聚合日志
docker compose logs -f service-hub-web  # Nginx 访问日志
docker compose restart       # 重启（数据在 HUB_HOST_DATA_DIR 中持久化）
docker compose stop          # 停止
docker compose up -d         # 启动
```

重启后上传的文件、插件 Build 与任务记录全部保留（数据目录持久化）。SPA 路由刷新
（如直接访问 `/jobs`）返回 200 页面，不会 404。

## 6. 管理台功能与对应 API

| 页面 | 功能 | 主要 API |
| --- | --- | --- |
| 概览 | 插件/Build/任务统计、最近任务、顶栏 10 秒健康轮询 | `GET /api/v1/plugins`、`/plugin-builds`、`/jobs` |
| 插件 | 上传 `.pypkg` 安装、查看 Build 轮询状态（INSTALLING→READY/FAILED）、启用/停用 | `POST /plugins/install`、`GET /plugin-builds/{id}`、`POST /plugin-builds/{id}/enable|disable` |
| 文件 | 上传、按 ID 查找、复制文件 ID、安全下载、本地最近 50 条记录 | `POST /files`、`GET /files`、`GET /files/{id}/download` |
| 新建任务 | Manifest 驱动表单/JSON 双模式、只允许 ENABLED Build 与其运行时、请求预览 | `POST /jobs` |
| 任务 | 最近 100 条任务、活动任务 2 秒刷新、耗时列 | `GET /jobs` |
| 任务详情 | 详情、日志增量拉取（游标去重）、确认取消、SUCCESS 后下载输出 | `GET /jobs/{id}`、`/logs`、`POST /cancel`、`/outputs` |
| 系统 | 版本、架构、Python、部署模式与 V1 无认证警告 | `GET /api/v1/system/info` |

不习惯图形界面时可继续使用 `tools/hubctl`（与 Web 完全等价）：

```bash
HUB_URL=http://127.0.0.1:8080 python tools/hubctl health
HUB_URL=http://127.0.0.1:8080 python tools/hubctl plugin install dist/nc_to_shp-1.0.0-linux-amd64-docker.pypkg
HUB_URL=http://127.0.0.1:8080 python tools/hubctl job run nc_to_shp --version 1.0.0 --runtime docker --input source_nc=file_1 --params params.json
```

注意 `hubctl` 也可以直接走 8080 的 Nginx 代理，或走 8000 的后端回环端口，两者等价。

## 7. Nginx 边界说明

- Web 容器内的 Nginx 只代理 `/api/v1/` 到 `http://service-hub:8000/api/v1/`，
  `/internal/v1/*` 返回 404；
- 上传体积 `client_max_body_size 10g`，读/发超时 3700 秒（与插件执行超时上限对齐）；
- 下载大文件关闭了 `proxy_buffering`，避免 Nginx 落盘临时文件；
- V1 仍无应用层认证：生产部署必须由宿主 Nginx/边界提供 TLS 与来源限制，管理台
  8080 与后端 8000 都只绑定 `127.0.0.1`，不要修改为对外监听。

## 8. 备份、升级与回滚

- 备份/迁移：与后端一致，只备份 `HUB_HOST_DATA_DIR` 指向的数据目录（见
  [单容器部署与脚本插件使用](单容器部署与脚本插件使用.md) 的迁移章节）；
- 升级：`docker compose stop` → 导入新镜像 → `docker compose up -d`；
- 回滚：保留旧版本镜像 tag，`docker compose stop` 后重新 `docker load` 旧镜像再启动；
- 生命周期管理只使用 `stop`/`restart`/`up -d`，禁止 `docker compose down -v`。

## 9. 常见错误

| 现象 | 检查命令 | 说明 |
| --- | --- | --- |
| 8080 打不开但 8000 正常 | `docker compose logs service-hub-web` | Web 容器未启动或 Nginx 配置错误 |
| 页面能开但接口 502 | `docker compose ps` | 后端未 healthy，Web 的 `depends_on: service_healthy` 等待中 |
| 上传大文件失败 | `docker compose logs service-hub-web` | 检查是否经 8080 代理上传（10g 限制）；或直接改走 8000 |
| 刷新 `/jobs/xxx` 返回 404 | `docker compose exec service-hub-web cat /etc/nginx/nginx.conf` | SPA fallback 配置丢失，确认 `try_files ... /index.html` |
| `/internal/v1` 返回了数据 | 立即停用 | 正常必须 404；说明 Nginx 配置被改动，恢复出厂 `web/nginx.conf` |
| Web 容器出现 /data 挂载 | `docker inspect service-hub-web` | 正常不应有任何挂载，检查 compose.yaml 是否被改动 |

## 10. 相关文档

- 后端单容器部署、hubctl/hub-plugin 命令行流程与数据迁移：
  [单容器部署与脚本插件使用](单容器部署与脚本插件使用.md)
- 插件协议与双运行时构建细节：[双运行时插件构建与部署](双运行时插件构建与部署.md)
