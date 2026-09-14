# V3.0 长期运行 Docker 服务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让管理员能在 Service Hub 内安全部署和运维长期运行的 Docker 服务。

**Architecture:** Hub API 维护 ServiceDefinition 并以内部令牌调用同容器回环 service-manager；service-manager 是唯一接触 Docker Socket 的子进程。React 管理台仅调用 Hub API。

**Tech Stack:** FastAPI、SQLAlchemy/Alembic、Pydantic、docker-py、Supervisor、React、TypeScript、Ant Design、Vitest。

**Spec:** docs/superpowers/specs/2026-09-14-v3-long-running-services-design.md

## Global Constraints

- Hub API 不得直接挂载或调用 Docker Socket。
- 仅管理员可变更服务定义或执行生命周期动作；读取接口按既有认证策略开放给已登录用户。
- 服务端口必须绑定 `127.0.0.1`；服务容器名必须为 `hub-svc-<name>`。
- 不改变现有 Job/插件 Runner 协议，也不得把服务容器当作插件运行时。
- 新增 API 错误使用既有 `{ "error": { "code", "message", "details" } }` 格式。

---

### Task 1: 服务 API 与 Hub 到 manager 的内部客户端

**Files:**
- Create: `packages/hub-server/src/hub_server/routers/services.py`
- Create: `packages/hub-server/src/hub_server/services/service_manager.py`
- Modify: `packages/hub-server/src/hub_server/main.py`
- Modify: `packages/hub-server/src/hub_server/schemas.py`
- Test: `tests/server/test_services_api.py`

**Interfaces:** 读取 `ServiceDefinition`；产生 `/api/v1/services` 生命周期 API 和 `ServiceManagerClient`。

- [ ] 写出管理员/非管理员、manager 502、创建后状态、日志 tail 的 API 失败测试。
- [ ] 运行 `pytest tests/server/test_services_api.py -v`，确认实现前失败。
- [ ] 以 httpx 回环客户端和 `HUB_RUNNER_TOKEN` 实现 manager 调用；写操作持久化、成功后审计并映射状态。
- [ ] 运行上述测试、`ruff check packages/hub-server tests/server/test_services_api.py` 与 `mypy packages/hub-server/src/hub_server`。
- [ ] 提交 `feat(server): add long-running service api`。

### Task 2: service-manager 与容器部署安全闭环

**Files:**
- Modify: `deploy/service_hub/service_manager_service.py`
- Modify: `deploy/service_hub/supervisord.conf`
- Modify: `Dockerfile`
- Test: `tests/deploy/test_service_manager.py`
- Test: `tests/deploy/test_healthcheck.py`

**Interfaces:** 消费 Task 1 的内部请求；保持 `/ping`、`/deploy`、`/{name}/status|logs|start|stop|restart`、`DELETE /{name}`。

- [ ] 增加令牌缺失/错误、回环端口、容器命名、回环端口映射、非 root UID、替换同名容器和状态/日志映射测试。
- [ ] 运行 `pytest tests/deploy/test_service_manager.py tests/deploy/test_healthcheck.py -v`，确认缺失行为失败。
- [ ] 最小化修正 manager、Supervisor 运行身份及健康检查，避免向 Hub API 暴露 Docker Socket。
- [ ] 重跑测试及 `ruff check deploy/service_hub tests/deploy/test_service_manager.py`。
- [ ] 提交 `fix(deploy): harden service manager runtime`。

### Task 3: 管理台服务页完成与测试

**Files:**
- Create: `web/src/pages/ServicesPage.test.tsx`
- Modify: `web/src/api/services.ts`
- Modify: `web/src/hooks/queryKeys.ts`
- Modify: `web/src/layouts/AppLayout.tsx`
- Modify: `web/src/routes/router.tsx`
- Modify: `web/src/pages/ServicesPage.tsx`

**Interfaces:** 消费 Task 1 JSON API；产生 `/services` 管理页。

- [ ] 为服务列表、普通用户无删除按钮、管理员删除确认、环境变量/挂载格式校验、日志 tail 与 502 文案写失败测试。
- [ ] 运行 `npm run test:run -- ServicesPage`，确认实现前失败。
- [ ] 完成当前未提交服务页改动，补足 API 类型与可访问标签，不在前端保存 Docker 凭据。
- [ ] 运行 `npm run test:run -- ServicesPage`、`npm run typecheck`、`npm run lint`、`npm run build`。
- [ ] 提交 `feat(web): add long-running service management page`。

### Task 4: V3 镜像验收与中文运维文档

**Files:**
- Create: `docs/service-docs/服务说明-V3.0.md`
- Create: `docs/service-docs/V3.0-部署验收清单.md`
- Modify: `README.md`
- Modify: `tests/integration/test_web_console_lifecycle.py`
- Modify: `tests/server/test_container_smoke.py`

**Interfaces:** 使用 Tasks 1–3 的部署产物；产生管理员部署、回环代理、升级与回滚说明。

- [ ] 增加镜像内 `service-manager` 存在、Docker Socket 仅 manager 使用、Compose 配置仍完整的测试。
- [ ] 运行受影响 Python 测试；Linux AMD64 Docker 真机项明确标记为部署门禁，不能伪装成 Windows 通过。
- [ ] 编写中文端到端步骤：登录、部署 `nginxdemos/hello`、访问回环端口、日志、停止/删除及失败排查。
- [ ] 运行 docs 链接/命令引用检查与 `docker compose config`（若当前环境有 Docker）。
- [ ] 提交 `docs: add v3 long-running services operations guide`。
