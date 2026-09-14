# V3 Task 1：服务 API 与 manager 内部客户端

## 完成内容

- 新增 `routers/services.py`：提供已登录用户可读、仅管理员可创建、更新、启停、重启和删除的 `/api/v1/services` API。
- 新增 `services/service_manager.py`：以 `httpx` 调用同容器回环 `127.0.0.1:8001`（可通过 `HUB_SERVICE_MANAGER_URL` 覆盖），并以配置中注入的 `HUB_RUNNER_TOKEN` Bearer 令牌鉴权。
- 新增请求/响应 Pydantic 模型，并把服务路由注册进应用。
- 写操作先保存期望配置；manager 失败时将期望状态持久化为 `STOPPED`，绝不会误标记为 `RUNNING`；成功操作均写入审计日志。
- 对 service-manager 连接、5xx 或非法响应统一返回 `SERVICE_MANAGER_UNAVAILABLE`（502）；容器找不到映射为统一 404 Hub 错误。
- 公共服务响应不回显环境变量或挂载内容，实时状态经 `runtime` 对象返回；日志使用纯文本响应以匹配管理台客户端。

## 变更文件

- `packages/hub-server/src/hub_server/routers/services.py`
- `packages/hub-server/src/hub_server/services/service_manager.py`
- `packages/hub-server/src/hub_server/main.py`
- `packages/hub-server/src/hub_server/schemas.py`
- `tests/server/test_services_api.py`

## 验证

- `.venv\\Scripts\\python.exe -m pytest tests/server/test_services_api.py -v`：4 passed。
- `.venv\\Scripts\\python.exe -m ruff check packages/hub-server tests/server/test_services_api.py`：通过。
- `.venv\\Scripts\\python.exe -m mypy packages/hub-server/src/hub_server`：通过（43 个源文件）。

测试覆盖管理员创建后的状态/审计、viewer 只读权限、manager 不可用的统一 502 及失败状态持久化、日志 `tail` 转发。

## 提交

- `feat(server): add long-running service api`

## 关注点

- Windows 本地仅进行了无 Docker 的单元验证；真实 Docker Socket、Supervisor 和 Linux AMD64 验收由后续部署任务负责。
- 工作树原有的 Task 3 前端未提交改动未被修改或暂存。
