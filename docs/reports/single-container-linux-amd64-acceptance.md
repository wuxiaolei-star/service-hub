# 单容器双运行时发布验收报告（Linux AMD64）

> 本报告对应 `docs/superpowers/plans/2026-09-12-single-container-service-hub.md` Task 10。
> 带有 `（待填写）` 的字段必须在原生 Linux AMD64 验收机上执行相应命令后填入真实值；
> 全部字段填写完毕且"未通过项"为空后，单容器 V1 才能宣布完成。

## 1. 环境与构建物

| 项目 | 值 |
| --- | --- |
| Git commit | （待填写：`git rev-parse HEAD`，应为 `feature/single-container-hub` 最新提交） |
| Linux 发行版与版本 | （待填写：`cat /etc/os-release`） |
| 内核架构 `uname -m` | （待填写：必须为 `x86_64`） |
| Docker 版本 | （待填写：`docker version --format '{{.Server.Version}}'`） |
| Compose 版本 | （待填写：`docker compose version`） |
| Hub 镜像 ID | （待填写：`docker image inspect -f '{{.Id}}' python-service-hub:1.0.0-linux-amd64`） |
| Hub 镜像 SHA256（save 后） | （待填写：`sha256sum service-hub-image.tar`） |

## 2. 插件构建物

| 项目 | 值 |
| --- | --- |
| `nc_to_shp-1.0.0-linux-amd64-conda.pypkg` SHA256 | （待填写：`sha256sum packages/nc-to-shp-plugin/dist/*.pypkg`） |
| `nc_to_shp-1.0.0-linux-amd64-docker.pypkg` SHA256 | （待填写：同上） |
| 构建命令 | `hub-plugin build packages/nc-to-shp-plugin --runtime conda-pack --arch amd64 --output packages/nc-to-shp-plugin/dist` 与 `--runtime docker` 两条，均须成功 |

## 3. 真实 NC 双运行时验收

执行：

```bash
HUB_NC_SAMPLE_PATH=/absolute/path/sample.nc \
HUB_PLUGIN_PACKAGE_DIR=/srv/python-service-hub/packages/nc-to-shp-plugin/dist \
HUB_BASE_URL=http://127.0.0.1:8000 \
python -m pytest -m integration tests/integration/test_dual_runtime_nc_to_shp.py -v
```

| 项目 | conda-pack | docker |
| --- | --- | --- |
| Job 状态 SUCCESS | （待填写） | （待填写） |
| build_id | （待填写） | （待填写） |
| job_id | （待填写） | （待填写） |
| source file_id | （待填写，两次运行共用同一 file_id） | 同左 |
| 输出 ZIP SHA256 | （待填写） | （待填写） |
| ZIP 组件集、PointZ、CRS(EPSG:4326)、字段、SMID、坐标、属性一致 | （待填写：`differences == []`） | 同左 |
| 要素数 39,464 | （待填写） | （待填写） |

## 4. 容器生命周期与权限边界验收

执行：

```bash
python -m pytest -m integration tests/integration/test_single_container_lifecycle.py -v
```

| 项目 | 结果 |
| --- | --- |
| `docker compose config --services` 仅含 `service-hub` | （待填写） |
| 容器 health 为 healthy，`restart` 后恢复 healthy | （待填写） |
| 重启后 GET 文件元数据仍 200（数据持久化） | （待填写） |
| supervisor 三进程 RUNNING（hub-api / conda-runner / docker-runner） | （待填写） |
| hub-api 访问 Docker Socket 得到 permission denied | （待填写） |
| conda-runner 访问 Docker Socket 得到 permission denied | （待填写） |
| docker-runner Docker ping 成功 | （待填写） |

## 5. 离线发布包验收

执行：

```bash
bash deploy/release/build-release.sh
mkdir -p /tmp/service-hub-release-check
tar -xzf dist/service-hub-1.0.0-linux-amd64.tar.gz -C /tmp/service-hub-release-check
cd /tmp/service-hub-release-check/service-hub-linux-amd64
sha256sum -c SHA256SUMS
sudo ./install.sh
sudo ./start.sh
sudo ./status.sh
```

| 项目 | 结果 |
| --- | --- |
| SHA256SUMS 全部 OK | （待填写） |
| `install.sh` 后 `.env` 仅含 `HUB_HOST_DATA_DIR` 且数据目录已创建 | （待填写） |
| `start.sh` 后单容器 healthy | （待填写） |
| 发布包 `dist/service-hub-1.0.0-linux-amd64.tar.gz` SHA256 | （待填写） |

## 6. Web 管理台（service-hub-web）验收

执行：

```bash
python -m pytest -m integration tests/integration/test_web_console_lifecycle.py -v
```

| 项目 | 结果 |
| --- | --- |
| Web 镜像 `python-service-hub-web:1.0.0-linux-amd64` SHA256 | （待填写：`docker image inspect -f '{{.Id}}'`） |
| `docker compose config --services` 仅含 `service-hub`、`service-hub-web` | （待填写） |
| `http://127.0.0.1:8080/` 返回 200 text/html | （待填写） |
| SPA 刷新 `/jobs/nonexistent` 返回 200 | （待填写） |
| 经 Nginx 代理的 `/api/v1/system/health` 返回 UP | （待填写） |
| `/internal/v1/*` 返回 404 | （待填写） |
| Web 容器无 /data 与 Docker Socket 挂载 | （待填写） |
| `docker compose restart` 后数据持久化（文件元数据仍 200） | （待填写） |
| 后端直连 `127.0.0.1:8000` 与代理 `127.0.0.1:8080` 健康均 UP | （待填写） |
| `status.sh` 同时输出 hub health 与 web health（8080） | （待填写） |

## 7. 非集成质量门（已在开发机验证，2026-09-13）

| 项目 | 结果 |
| --- | --- |
| `python -m pytest -m "not integration"` | 通过（本仓库全量非集成测试） |
| `python -m ruff check .` | 通过 |
| `python -m mypy packages` | 通过 |
| `HUB_HOST_DATA_DIR=/srv/service-hub-data docker compose config --quiet` | 通过 |
| `tests/deploy/test_documented_commands.py`（文档一致性，含 Web 章节） | 通过 |
| `cd web && npm ci && npm run test:run && npm run typecheck && npm run lint && npm run build` | 通过 |

## 8. V2.0 认证与审计验收

执行：

```bash
HUB_BOOTSTRAP_PASSWORD=<bootstrap-admin.json 中的口令> python -m pytest -m integration tests/integration/test_auth_lifecycle.py -v
```

| 项目 | 结果 |
| --- | --- |
| required 模式下未认证请求返回 401（health 除外） | （待填写） |
| bootstrap-admin.json 生成且权限 0600 | （待填写） |
| 初始 admin 登录成功、role=admin、must_change_password=true | （待填写） |
| logout 后 token 立即失效 | （待填写） |
| 登录成功/失败均落审计日志 | （待填写） |
| `HUB_AUTH_MODE=off` 时行为与 V1 等价 | （待填写） |
| hubctl login/whoami/logout 流程 | （待填写） |

## 9. V2.1 配额、清理与备份验收

执行：

```bash
HUB_BOOTSTRAP_PASSWORD=<口令> python -m pytest -m integration tests/integration/test_quota_lifecycle.py -v
```

| 项目 | 结果 |
| --- | --- |
| 上传超配额返回 409 QUOTA_EXCEEDED（含 used/limit） | （待填写） |
| 并发 Job 超限返回 409 | （待填写） |
| admin 豁免与 `quotas.enabled=false` 直通 | （待填写） |
| `GET /users/{id}/usage` 数据正确 | （待填写） |
| cleaner 为第四个受管进程且 RUNNING | （待填写） |
| TTL 过期未引用输入被真实删除并写审计 | （待填写） |
| `GET /system/metrics` admin 可抓取且格式为 Prometheus 文本 | （待填写） |
| `hubctl backup create` 生成备份且超出份数被修剪 | （待填写） |
| Web 用户页用量列与系统页指标卡渲染 | （待填写） |

## 10. V2.2 自动化闭环验收

执行：

```bash
HUB_BOOTSTRAP_PASSWORD=<口令> python -m pytest -m integration tests/integration/test_automation_lifecycle.py -v
```

| 项目 | 结果 |
| --- | --- |
| scheduler 为第五个受管进程且 RUNNING | （待填写） |
| 创建带 callback 的 Job → 终态后收到签名 POST（本地 stub 校验 HMAC） | （待填写） |
| 回调失败退避重试 3 次后 EXHAUSTED | （待填写） |
| 定时任务到点自动创建 Job 且 next_run_at 推进 | （待填写） |
| 两步管道 execute 后自动步进，$prev 替换为实际 file_id | （待填写） |
| 管道步骤失败整条 FAILED 且不再步进 | （待填写） |
| Web 定时任务/管道/回调卡片渲染正常 | （待填写） |

## 11. 未通过项

（必须为空；如有未通过项，逐条记录现象、原因与修复 commit 后复验）
