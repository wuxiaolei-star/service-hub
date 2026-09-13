# Service Hub V2.1 资源治理与可观测实施计划

> 依据：`docs/superpowers/specs/2026-09-13-quotas-retention-metrics-v2.1-design.md`
> 分支：`feature/v2-auth-rbac-audit`（继续堆叠）

**Goal:** 在 V2.0 认证体系上交付配额（上传/建 Job 执行点）、文件生命周期清理
（cleaner 进程）、Prometheus `/metrics` 端点与 hubctl 备份命令。

## Global Constraints

- 既有 API 字段零改动；新配置节 strict 校验；
- admin 不受限、`quotas.enabled=false` 直通；
- 清理循环不得删除 OUTPUT 或被引用文件；每轮上限 500；
- 每任务 TDD + 独立提交。

---

### Task 1: 配置与数据模型

- Create `tests/server/test_quota_settings.py`、`tests/server/test_quota_models.py`
- Modify `settings.py`（QuotasSettings/RetentionSettings）、`models.py`（users 三配额列、
  file_records.owner_user_id）、alembic 0004

### Task 2: 配额强制

- Create `services/quotas.py`、`tests/server/test_quota_enforcement.py`
- Modify `routers/files.py`（上传写入 owner + 超限 409）、`routers/jobs.py`（并发超限 409）
- Produces: `QUOTA_EXCEEDED`(409, details=quota/used/limit)；admin 豁免；开关直通

### Task 3: 用量查询端点

- Create `routers/users.py` 增 `GET /users/{id}/usage`（admin）；Create
  `tests/server/test_usage_api.py`
- Produces: `{total_bytes, file_count, active_jobs}`

### Task 4: 清理循环（cleaner 进程）

- Create `services/retention.py`、`deploy/service_hub/cleaner_service.py`、修改
  supervisord.conf；Create `tests/server/test_retention.py`、
  `tests/deploy/test_cleaner_process.py`
- 规则：INPUT + 超 TTL + 无 job_files 引用 → 删除（审计 actor=system）；OUTPUT/被引用
  跳过；每轮 ≤500；备份数量修剪（保留 3）

### Task 5: /metrics 端点

- Create `services/metrics.py`、Modify `routers/system.py`（admin）；Create
  `tests/server/test_metrics_api.py`
- Produces: Prometheus 文本（jobs/files/bytes/builds/users/sessions/uptime）

### Task 6: 备份

- Create `services/backup.py`、Modify `routers/system.py` 增 `POST /system/backup`；
  Modify `tools/hubctl` 增 `backup create|restore`；Create
  `tests/server/test_backup_api.py`、`tests/tools/test_hubctl_backup.py`
- Produces: 服务端 tar.gz 落 `data/backups/`（走既有下载通道），审计记录，hubctl 恢复
  打印步骤清单

### Task 7: Web 管理台

- Modify UsersPage（用量列）、SystemPage（指标卡）；Create 对应测试

### Task 8: 文档与验收

- Modify `config/hub.yaml.example`、`docs/guides/单容器部署与脚本插件使用.md`、
  README、验收报告 V2.1 节；Create `tests/integration/test_quota_lifecycle.py`
  （超限 409 → 清理 TTL 文件 → metrics 变化 → 备份可下载）；输出
  `docs/service-docs/服务说明-V2.1.md`

## 执行顺序

Task 1→2→3 为治理链（必须连续）；4、5、6 可并行；7、8 收尾。全部完成后运行
全量质量门并输出 V2.1 服务说明。
