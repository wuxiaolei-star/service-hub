# Service Hub V3.0 长期运行 Docker 服务设计

- 日期：2026-09-14
- 状态：已确认（按用户授权自主推进，路线图暂定终版）
- 依据：`docs/design/路线图-V2.0至V3.0.md` V3.0 章节
- 上游基线：V2.3（`feature/v2-auth-rbac-audit`）

## 1. 目标与边界

Hub 从"一次性任务调度器"升级为可托管**常驻插件服务**（推理/查询/瓦片等服务容器）
的轻量应用平台，同时一次性 Job 能力原样保留。

本轮交付：服务定义 CRUD、部署/启停/重启/删除、状态与日志查看、端口登记去重、
管理台"服务"页与 hubctl `service` 命令。**不做**：compose 编排、服务发现、
多主机调度、构建镜像（镜像由发布者预构建并 `docker load` 到宿主机）。

## 2. 架构决策（保持最小特权边界）

- 新增第六受管进程 **`service-manager`**（专用系统用户 `service-mgr`）：
  唯一获得 Docker Socket 的新增面，FastAPI 应用监听容器内 `127.0.0.1:8001`，
  用既有 `HUB_RUNNER_TOKEN` Bearer 认证；无状态执行器（期望状态由 hub-api 下发）；
- **hub-api 进程本身仍然没有 Docker Socket**：管理请求经内部 HTTP 客户端
  （127.0.0.1:8001 + Runner Token）转发；
- docker-runner 的 Job 隔离边界不变；常驻服务容器**绝不挂载 Docker Socket**，
  端口仅发布到宿主机 `127.0.0.1`。

~~~text
浏览器/hubctl → hub-api(:8000, admin/operator) ──内部HTTP(127.0.0.1:8001, RunnerToken)──► service-manager ──► Docker Engine
                                                                    （专用 service-mgr 用户，唯一新增 Socket 面）
~~~

## 3. 数据模型（迁移 0006）

~~~text
service_defs   id, name UNIQUE(≤63, DNS 风格), image, container_name UNIQUE
               (默认 hub-svc-<name>), ports_json([{host, container}]),
               env_json({k:v}), mounts_json([{source(绝对路径), target, read_only}]),
               command_json(可空), user_label(可空, 默认 65532:65532),
               desired_state(RUNNING|STOPPED), created_at, updated_at
~~~

运行时实际状态不落库——由 service-manager 实时查 Docker（inspect）。端口去重：
创建/更新时校验 host 端口不与其他 service_def 冲突（409 `PORT_CONFLICT`），
也不允许占用 8000/8001/8080。

## 4. 接口

### 4.1 service-manager 内部 API（Runner Token）
- `GET /ping`；`POST /deploy`（body：name/image/ports/env/mounts/command/user/
  restart_policy={"Name":"unless-stopped"}；容器名固定 `hub-svc-<name>`；已存在同名
  容器则按新 spec 重建：先 remove 后 run）；
- `POST /{name}/stop|start|restart`；`DELETE /{name}`（remove）；
- `GET /{name}/status`（docker inspect：state/health/ports/created）；
- `GET /{name}/logs?tail=200`（text/plain）。

### 4.2 hub-api 公共 API（/api/v1）
- `GET /services`（viewer）：定义 + 实时状态合并；
- `POST /services`、`PUT /services/{name}`（operator+，端口校验后写 def；desired=
  RUNNING 时即时 deploy）；
- `POST /services/{name}/stop|start|restart`（operator+，更新 desired_state 并执行）；
- `DELETE /services/{name}`（admin，remove 容器 + 删 def）；
- `GET /services/{name}/logs`（operator+，代理 service-manager，text/plain）；
- 全部写操作审计（action=`service.deploy|stop|start|restart|delete`）；
- 错误码：`SERVICE_NAME_TAKEN`(409)、`PORT_CONFLICT`(409)、`SERVICE_NOT_FOUND`(404)、
  `SERVICE_MANAGER_UNAVAILABLE`(502)。

## 5. hubctl

`service list|deploy <name> --image ... --port host:container(可重复) --env k=v(可重复)
--mount src:target[:ro](可重复)|stop|start|restart|remove|logs <name> [--tail N]`。

## 6. 依赖与部署

- hub-server pyproject 增 `docker>=7.1,<8`（service-manager 使用；烘焙进离线镜像）；
- supervisord 第六进程 `[program:service-manager]`（用户 `service-mgr`，Dockerfile
  增建该系统用户并加入 docker-socket 组——由 entrypoint 动态 socket GID 逻辑复用）；
- healthcheck 必需清单扩为六进程；
- 镜像要求：服务镜像需先存在于宿主 Docker（`docker load` 或 pull），service-manager
  不做构建/拉取。

## 7. 测试与验收

- service-manager：Docker SDK 客户端注入 mock——deploy/stop/start/restart/remove/
  status/logs 全覆盖 + Token 401；
- hub-api：CRUD/动作/端口冲突/审计/`SERVICE_MANAGER_UNAVAILABLE` 透传 502；
- 集成（Linux）：部署 `nginx:alpine` 样例服务 → 127.0.0.1 端口可达 → stop → restart
  → logs → remove；socket 越权检查（hub-api 用户仍无法直接 docker）。
