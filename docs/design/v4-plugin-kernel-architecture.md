# Service Hub V4.0 功能插件化架构设计

> 综合：内核协议设计推演 + 迁移风险评估 + Cordis/DeepSeek Harness 理念研究
> 分支：`feature/v2-auth-rbac-audit` → 新分支 `feature/v4-plugin-kernel`
> 前置：V3.3（本分支 HEAD b8d5635）

---

## 1. 核心理念（来自 Cordis / DeepSeek Harness）

| Cordis 理念 | 映射到 Service Hub |
|---|---|
| **可逆效果**（Removal is total） | 插件禁用 = 路由 404 + 定时任务注销 + 事件解绑 + 菜单隐藏，零残留 |
| **反应式依赖** | 依赖被禁用时，依赖方收到通知并优雅降级（不是崩溃） |
| **命名换装点** | `storage: local|s3`、`auth: local|ldap`、`database: sqlite|pg`——配置切换 |
| **作用域服务** | 每个插件的生命周期由内核 fiber 追踪，卸载即回收 |
| **一切皆插件** | 连 hub-api 的健康检查端点也是内核自带插件 |

来源：[DeepSeek Harness](https://www.deepseek.com) · [Cordis 框架](https://www.npmjs.com/package/@deepseek-ai/cordis) · [floatboat.ai 分析](https://floatboat.ai/blog/cordis-plugin-framework) · [ContextOS 安全审查](https://contextosai.com/blog/deepseek-harness-architecture-security-review)

---

## 2. 架构分层

```text
┌────────────────────────────────────────────────────────────────┐
│                       Kernel（不可替换底座）                     │
│                                                                │
│  ┌────────────┐ ┌────────────┐ ┌──────────────────────────┐   │
│  │ HTTP Server│ │ Database   │ │ Plugin Registry          │   │
│  │ (FastAPI)  │ │ (SQLite)   │ │ (发现/拓扑排序/生命周期)    │   │
│  └────────────┘ └────────────┘ └──────────────────────────┘   │
│  ┌────────────┐ ┌────────────┐ ┌──────────────────────────┐   │
│  │ Auth       │ │ Event Bus  │ │ Config (strict + env     │   │
│  │ Context    │ │ (outbox +  │ │ override + per-plugin    │   │
│  │ (接口)     │ │  in-proc)  │ │ sections)                │   │
│  └────────────┘ └────────────┘ └──────────────────────────┘   │
│  ┌────────────┐ ┌──────────────────────────────────────────┐  │
│  │ Scheduler  │ │ Named Swap Points                        │  │
│  │ Framework  │ │ storage / auth / database / notify       │  │
│  └────────────┘ └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
     │ FeaturePlugin Protocol（注册/发现/生命周期/事件）
┌────┴───────────────────────────────────────────────────────────┐
│ Base Plugins（必须，不可禁用）│ Extension Plugins（可启停/降级） │
├──────────────────────────────┼─────────────────────────────────┤
│ auth  files  jobs  catalog   │ webhooks  schedules  pipelines  │
│ audit (Phase 1 内核横切)      │ services  backup  quotas       │
└──────────────────────────────┴─────────────────────────────────┘
```

### 命名去歧义

| 概念 | 术语 | 说明 |
|---|---|---|
| 内核功能插件 | **FeaturePlugin** | 平台自身功能域（auth/files/jobs…） |
| 业务插件（现有） | **Compute Plugin** | 用户上传的算法插件（.pypkg），由 catalog 域管理 |
| 两者永不混淆 | `plugins.enabled` 指 FeaturePlugin；Compute Plugin 由 catalog 域管理 |

---

## 3. FeaturePlugin 协议（完整代码见子智能体推演报告）

```python
class BaseFeaturePlugin:
    name: str                                # 唯一标识
    depends_on: tuple[str, ...]              # 硬依赖（缺失则拒绝启动）
    soft_depends_on: tuple[str, ...]         # 软依赖（缺失则 WARN 降级）
    models: tuple[type, ...]                 # 自有 ORM 模型
    default_enabled: bool = True

    routers: tuple[RouterMount, ...]         # HTTP 路由 + 前缀
    scheduler_tasks: tuple[SchedulerTaskSpec, ...]  # 周期任务（声明执行进程）
    event_handlers: tuple[EventBinding, ...] # 事件订阅（含跨进程投递标记）

    def on_load(self, ctx: PluginContext) -> None: ...
    def bootstrap(self, ctx: PluginContext) -> None: ...
```

关键类型：
- `EventBus`：进程内同步分发 + SQLite outbox 跨进程投递（at-least-once，≤30s）
- `SchedulerTaskSpec`：声明执行进程（hub-api/scheduler/cleaner）
- `PluginContext`：settings + session_factory + storage + event_bus + process

完整协议代码：子智能体推演报告（`packages/hub-server/src/hub_server/kernel/protocol.py`）

---

## 4. 插件归属表（基于真实 import 分析）

| 插件 | routers | models | scheduler_tasks | depends_on |
|---|---|---|---|---|
| **auth** | auth, users(+key_router) | UserRecord, SessionRecord, ApiKeyRecord, LoginAttemptRecord | delete_expired_sessions | — |
| **files** | files.py | FileRecord | — | auth |
| **catalog** | plugins.py, registry.py | Plugin, PluginVersion, PluginBuild, Environment, RunnerOperation | — | — |
| **jobs** | jobs.py, internal_runner.py, logs_stream.py | Job, JobFile | reap_lost_jobs, delete_retired_jobs | auth, files, catalog |
| **audit** | audit.py | AuditLogRecord | delete_old_audit_logs | — |
| **webhooks** | webhooks.py | JobCallback, WebhookDelivery | deliver_callbacks | jobs |
| **schedules** | schedules.py | Schedule | trigger_schedules | — (soft: jobs) |
| **pipelines** | pipelines.py | Pipeline, PipelineRun | advance_pipelines | — (soft: jobs) |
| **services** | services.py | ServiceDef | reconcile_service_containers | — |
| **backup** | (system.py 内) | — | (backup 由手动/API 触发) | files |
| **quotas** | (无独立 router，寄生于 files/jobs/users) | — | — | files, jobs |
| **metrics** | (system.py 内) | — | — | — |
| **core（内核保留）** | system.py | HubEventRow（新增） | drain_events | — |

> **命名变更**：现有 `routers/plugins.py`（管理 .pypkg 计算插件）→ 改名 `catalog` 域，
> 避免与 FeaturePlugin 混淆。

---

## 5. 迁移风险评估（必须先解决的三个硬伤）

| # | 风险 | 严重度 | 解决阶段 |
|---|---|---|---|
| 1 | `jobs.pipeline_run_id → pipeline_runs.id` 反向 FK（核心→扩展），禁用 pipelines 时 FK 阻塞 | 生产在线 DDL | Phase 1 前置：解除 FK，pipelines 自持映射表 |
| 2 | `extra=forbid` 配置锁死：禁用插件后 hub.yaml 多余节导致启动失败 | 全站不可用 | Phase 1：两阶段解析（禁用插件节=WARN 忽略） |
| 3 | 同事务一致性：audit 同事务写 + 配额同事务校验 + retention 靠 FK RESTRICT 编排删除顺序 | 静默改变失败语义 | 逐处显式决策：核心域保留同事务，扩展域接受最终一致 |

### 隐式耦合清单（来自迁移风险报告）

**Router → Router**（1 处）：
- `jobs.py:20` → `files.py:_file_response`（解法：下沉到 `schemas.py`）

**Service → Service 跨域**（3 处）：
- `pipelines.py:16` → `jobs.JobService`（管道步进建 Job）
- `schedules.py:15` → `jobs.JobService`（定时触发建 Job）
- `retention.py:14` → `services/audit.py`（横切）

**进程级聚合**（1 处）：
- `scheduler_service.py` 静态注册 8 个任务横跨 6 个域——插件化后改为插件自注册

**横切调用**（17 处）：
- `services/audit.record` 被 11 个 router + 6 个 service import——Phase 1 收编为内核 AuditSink 接口

---

## 6. 插件间通信

### 事件总线（双通道）

```python
# hub-api 进程内同步分发（不阻塞业务事务）
event_bus.emit("job.completed", payload, session=session, persist=True)
# → SQLite hub_events 表（outbox，同事务）+ 进程内同步 dispatch

# scheduler 进程 30s 轮询补投递
event_bus.drain(session)  # 处理 dispatched_at IS NULL 的事件
```

### 服务端口接口（同进程同事务）

```python
# quotas 需要同事务校验配额（不能走事件/HTTP）
class StorageUsageReader(Protocol):
    def get_usage(self, session: Session, user_id: int) -> UsageSummary: ...

# files 插件实现该端口
class FilesPlugin(BaseFeaturePlugin):
    def on_load(self, ctx: PluginContext):
        ctx.register_port("files.usage_reader", FileUsageReaderImpl(...))

# quotas 插件通过端口接口调用（同进程同事务，不跨 HTTP）
class QuotasPlugin(BaseFeaturePlugin):
    depends_on = ["files"]
    def on_load(self, ctx: PluginContext):
        self.usage_reader = ctx.get_port("files.usage_reader", StorageUsageReader)
```

---

## 7. 迁移策略

| 规则 | 说明 |
|---|---|
| 0001–0009 视为 core | 一字不动，无插件前缀 |
| 插件迁移命名 | `{seq:04d}_{plugin}_{slug}`（如 `0010_kernel_event_log`） |
| 迁移与启用正交 | `upgrade head` 永远跑全链，禁用插件不 skip/downgrade |
| head 分叉防护 | 启动时对比 `alembic_version` 与 `versions/` 目录 |
| per-plugin 分支迁移 | 推迟到 V5（第三方独立安装需求出现时） |

---

## 8. 分阶段实施

### Phase 1 — 内核 + 首批拆分（schedules + webhooks）
1. 内核：PluginRegistry + EventBus（outbox）+ FeaturePlugin 协议 + 配置两阶段解析
2. 前置迁移：解除 `jobs.pipeline_run_id` FK（0010）
3. 收编 audit 为内核 AuditSink（37 行原地收编，不改存储）
4. 拆 `schedules` 插件（零 FK、零迁移成本，验证插件机制四接触面）
5. 拆 `webhooks` 插件（验证事件解耦：Job 终态 → 事件 → 投递）
6. 前端：`/capabilities` 端点 + 动态菜单/路由 + `automation.ts` 拆分

### Phase 2 — 拆 pipelines + quotas
- pipelines：FK 解除后自持映射表
- quotas：users 表三个配额列迁到 quotas 自有表（迁移 0011）

### Phase 3 — 拆 services + backup + metrics
- 剩余扩展域逐个拆分，每个独立可启停

### Phase 4 — 第三方插件（V5 远期）
- 签名 + 沙箱 + 独立安装

---

## 9. Plugin → 依赖图

```text
auth ← files ← jobs ← webhooks
  ▲               ▲
  └── catalog ────┘ (jobs 依赖 catalog 的 Build)
```

拓扑排序：auth → files → catalog → jobs → webhooks / pipelines / schedules

---

## 10. 验收标准

- [ ] 禁用 schedules 插件后：`/schedules` 路由 404、菜单隐藏、scheduler 不执行 trigger
- [ ] 重新启用后：数据保留（不丢历史）、功能恢复
- [ ] 禁用 webhooks 后：创建 Job（无 callback）正常、callback 事件不投递
- [ ] 依赖校验：`enabled=[files]` 缺 auth → 启动报错列出缺失边
- [ ] `extra=forbid` 下禁用插件的配置节 → WARN 不阻断
- [ ] 全量非集成测试通过（841+）
- [ ] Linux AMD64 集成验收通过
