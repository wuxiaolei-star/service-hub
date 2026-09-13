# Service Hub V3.0 长期运行 Docker 服务实施计划

> 依据：`docs/superpowers/specs/2026-09-14-long-running-docker-services-v3.0-design.md`
> 分支：`feature/v2-auth-rbac-audit`。Task 1 主会话（模型/迁移/进程骨架/依赖）；
> Task 2/3 串行子智能体（hub-api 侧依赖 service-manager 客户端）；Task 4 Web 子智能体
> 与 Task 3 并行或接续；Task 5 主会话挂载/回归/文档。

### Task 1（主会话）: 基础设施
- pyproject 增 docker SDK；迁移 0006 `service_defs`；模型 `ServiceDef`
- `deploy/service_hub/service_manager_service.py` 骨架（FastAPI :8001 + RunnerToken
  认证 + 路由占位，Docker 客户端注入点）
- Dockerfile 增 service-mgr 用户；supervisord 第六进程；healthcheck 六进程；
  compose/socket 不变；main.py 内部客户端挂载点

### Task 2（子智能体 A）: service-manager Docker 执行器
- 实现 deploy/stop/start/restart/remove/status/logs/ping（Docker SDK，客户端工厂
  可注入 mock）；内部客户端 `hub_server/services/service_manager_client.py`
  （http.client 127.0.0.1:${HUB_SERVICE_MANAGER_PORT:-8001} + RunnerToken）
- 测试：tests/deploy/test_service_manager.py、tests/server/test_service_manager_client.py

### Task 3（子智能体 B，Task 2 完成后）: hub-api 服务管理端点
- Create `routers/services.py`（CRUD/动作/logs，矩阵见 spec）+ `services/service_defs.py`
  （DB CRUD + 端口去重 + client 转发 + 审计）
- 测试：tests/server/test_services_api.py

### Task 4（子智能体 C，可与 3 并行）: Web 服务页
- `web/src/pages/ServicesPage.tsx`（列表+状态 Tag+部署 Modal+启停重启删除+日志查看）
- 路由 `/services`、菜单"服务"；测试

### Task 5（主会话）: 挂载与收尾
- main.py 挂载 services router；全量回归；文档（指南/README/验收报告 V3 节）；
  `服务说明-V3.0.md`；修改记录；compose/发布包无变化说明

## 已知取舍
- 服务镜像不由 Hub 构建（发布者 docker load）；端口仅发布 127.0.0.1；
- desired_state 持久化，容器意外退出依赖 Docker restart 策略 + 手动 start；
  不做自动拉起守护。
