# Service Hub V4.0 Phase 1 功能插件化内核实施计划

> 依据：`docs/design/v4-plugin-kernel-architecture.md`
> 分支：`feature/v2-auth-rbac-audit`（继续堆叠）
> 目标：内核落地 + 首批两个插件拆分（schedules + webhooks），验证插件机制四接触面

## Global Constraints

- 业务逻辑零改动：现有 service 层代码只做"包装为插件"，不重写
- 既有 API 字段零删改；`total`/`replayed_from` 等纯增量
- `extra=forbid` 保留；两阶段解析保证禁用插件配置节 WARN 不阻断
- 每任务 TDD + 独立提交
- web/ 前端改动限定在路由/菜单动态化 + automation.ts 拆分

---

### Task 1（主会话）: 内核基础设施
- Create `packages/hub-server/src/hub_server/kernel/__init__.py`
- Create `kernel/protocol.py`（FeaturePlugin/BaseFeaturePlugin/EventEnvelope/EventBinding/SchedulerTaskSpec/RouterMount/PluginContext）
- Create `kernel/events.py`（EventBus：进程内同步 + SQLite outbox 持久化 + drain）
- Create `kernel/registry.py`（resolve_plugins：拓扑排序 + 依赖闭包校验 + 启用集合解析）
- Create `kernel/settings.py`（PluginsSettings：enabled 白名单 + options per-plugin）
- Tests：tests/server/test_kernel_registry.py（拓扑排序/依赖闭包/禁用/未知插件/环检测）

### Task 2（主会话）: 前置迁移 + 耦合解除
- Create `alembic/versions/0010_drop_pipeline_run_fk.py`（解除 jobs.pipeline_run_id FK + 索引）
- Modify `models.py`：Job.pipeline_run_id 去 ForeignKey 保留列（改注释为逻辑引用）
- Modify `routers/jobs.py`：`_file_response` import 下沉到 `schemas.py`
- Tests：确认既有测试全通过

### Task 3（子智能体 A）: 拆 schedules 插件
- Create `packages/hub-plugins/hub-plugin-schedules/`（独立包）
- 内容：plugin.py（FeaturePlugin 子类，声明 models/routers/tasks）+ 从 routers/schedules.py 和 services/schedules.py 迁入代码
- Modify main.py：改为 Plugin Registry 发现 + 挂载
- Tests：现有 test_schedules_api.py + test_schedule_trigger.py 保持通过

### Task 4（子智能体 B）: 拆 webhooks 插件
- Create `packages/hub-plugins/hub-plugin-webhooks/`
- 内容：plugin.py + 从 routers/webhooks.py 和 services/webhooks.py 迁入
- jobs.py 的 callback 登记改为 event_bus.emit("job.created_with_callback", ...)
- Tests：现有 webhook 测试保持通过

### Task 5（主会话）: 全量回归 + 文档 + 服务说明-V4.0-alpha + 修改记录

---

## 执行顺序

Task 1 → Task 2 → Task 3 + Task 4（并行）→ Task 5
