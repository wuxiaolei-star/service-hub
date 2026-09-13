# Service Hub V2.1 资源治理与可观测设计

- 日期：2026-09-13
- 状态：草案（待用户确认）
- 依据：`docs/design/路线图-V2.0至V3.0.md` V2.1 章节
- 上游基线：V2.0（认证 + RBAC + 审计，`feature/v2-auth-rbac-audit`）

## 1. 背景与目标

V2.0 引入用户主体后，多用户共用一个离线 Hub 立即产生三个治理问题：存储会被上传
无限膨胀、单个用户可以占满并发 Job、运维缺少健康与容量观测手段。本版本交付：

1. **配额**：全局默认 + 用户级覆盖，执行点为上传与建 Job；
2. **文件生命周期**：Job 输入文件 TTL 清理，被输出引用的文件受保护；
3. **`/metrics`**：Prometheus 文本格式的可观测端点；
4. **备份自动化**：`hubctl backup create|restore`。

本轮不实现：用户级权限变化、文件回收站、历史数据归档、Grafana 模板交付。

## 2. 已确认决策

| 决策 | 结论 |
| --- | --- |
| 清理循环进程形态 | supervisord 第四个受管进程 `cleaner`（崩溃隔离，重启语义清晰） |
| 指标暴露方式 | `GET /api/v1/system/metrics`，admin 角色（复用现有 Bearer 认证，无需新暴露面） |
| 配额默认 | 全局默认来自 hub.yaml；用户级覆盖存 users 表可空列；缺省不限制 |
| 备份形态 | hubctl 子命令，SQLite backup API + 数据目录文件树复制；恢复脚本输出命令清单由运维执行 |

## 3. 配额设计

~~~yaml
# hub.yaml 新增节（strict，extra=forbid）
quotas:
  enabled: true
  max_total_bytes: 1099511627776     # 全局+每用户总存储，默认 1TiB
  max_file_count: 10000              # 每用户文件数上限
  max_concurrent_jobs: 8             # 每用户并发（非终态）Job 上限
~~~

- 执行点 1 `POST /api/v1/files`：超出（用户已用字节/文件数 或 全局字节）返回
  `QUOTA_EXCEEDED`(409)，`details` 携带 `quota/used/limit`；
- 执行点 2 `POST /api/v1/jobs`：用户非终态 Job 数 ≥ 上限返回同错误；
- 统计口径：`FileRecord` 按 owner 汇总（新增 owner_user_id 列，见 §4）；
- admin 角色不受限（引导与运维场景）；`quotas.enabled=false` 时全部直通；
- 管理台"用户管理"页展示每用户已用字节/文件数（新查询端点
  `GET /api/v1/users/{id}/usage`）。

## 4. 数据模型（Alembic 迁移 0004）

- `users` 增列：`quota_total_bytes BIGINT NULL`、`quota_file_count INT NULL`、
  `quota_concurrent_jobs INT NULL`（NULL=用全局默认）；
- `file_records` 增列：`owner_user_id INT NULL`（引用 users.id，SET NULL），历史数据
  迁移时置 NULL（视为公共文件，仅计入全局字节）；
- 上传时写入 `owner_user_id = actor.id`（api_key 上传时为其创建者？V2.1 简化：api_key
  actor 记 NULL，仅计全局）。

## 5. 文件生命周期与清理循环

- 设置：hub.yaml `retention:{input_ttl_hours: 720, sweep_interval_minutes: 30}`；
- 规则：`role=INPUT` 且创建时间早于 TTL 且**未被任何 Job 引用**的 UPLOAD 文件可删除
  （记录 + 磁盘文件）；`role=OUTPUT` 不清理；被 Job 输入引用（job_files 关联存在）
  的文件不清理；
- 进程：supervisord 新增 `[program:cleaner]`（hub-data 组权限，独立于 hub-api），
  循环间隔 `sweep_interval_minutes`，删除动作写审计（actor_type=system）；
- 每轮清理上限 500 个文件，防止长事务。

## 6. /metrics 端点

`GET /api/v1/system/metrics`（admin）输出 Prometheus 文本格式（手写 exposition，
不新增依赖）：

~~~text
hub_jobs_total{status="..."} N
hub_jobs_active N
hub_files_total N
hub_files_bytes_total N
hub_plugin_builds_total{status="..."} N
hub_users_total N
hub_sessions_active N
hub_quota_global_bytes_total N
hub_uptime_seconds N
~~~

管理台"系统"页展示其中关键项（文件字节/Job 状态分布）。

## 7. 备份自动化

- `hubctl backup create --output <dir>`：调用 `GET /api/v1/system/backup`（admin）
  —— 服务端执行 SQLite `backup()` 到临时文件 + 打包数据目录为 tar.gz（不含
  environments 可重建内容），返回流式下载？——**简化**：直接在服务端生成
  `<data>/backups/backup-<ts>.tar.gz` 并返回文件 id 走既有下载通道；
- `hubctl backup restore`：V2.1 只打印恢复步骤清单（停止→解包→权限→启动），
  不自动执行破坏性操作；
- 每次备份动作写审计；备份数量上限（默认保留 3 份，超出删除最旧）由清理循环顺带执行。

## 8. 兼容与迁移

- 迁移 0004 仅增列/建表，不改既有列；
- 所有新端点沿用角色矩阵与错误信封；新配置节 strict 校验；
- Web：用户管理页增加用量列；系统页增加指标卡；无路由结构变化。

## 9. 测试与验收

- 单元：配额计算（用户/全局/admin 豁免/关闭开关）、TTL 清理规则（引用保护/OUTPUT
  保护/上限）、metrics 文本格式、备份打包内容；
- API：超限 409 与 details、usage 端点、metrics admin 网关；
- Linux AMD64：cleaner 进程状态、TTL 清理真实执行、备份文件完整性、metrics 抓取样例。
