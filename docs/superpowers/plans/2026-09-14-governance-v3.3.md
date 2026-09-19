# Service Hub V3.3 治理完善实施计划

> 依据：`docs/superpowers/specs/2026-09-14-governance-v3.3-design.md`
> 分支：`feature/v2-auth-rbac-audit`。迁移 0009 仅新表。三个子智能体并行。

### Task 1（子智能体 A）: 备份预检 + 服务 reconcile
- Modify：`services/backup.py`（disk_usage 预检）、`scheduler_service.py`（注册 reconcile）
- Create：`services/reconcile.py`（reconcile_services）
- Tests：tests/server/test_backup_precheck.py、tests/server/test_service_reconcile.py

### Task 2（子智能体 B）: Webhook 投递明细
- Modify：`models.py`（WebhookDelivery）、`services/webhooks.py`（_deliver_one 写明细）、
  `routers/webhooks.py`（GET deliveries）
- Create：`alembic/versions/0009_webhook_deliveries.py`
- Tests：tests/server/test_webhook_deliveries.py

### Task 3（子智能体 C）: 文件删除 + CSV 导出
- Modify：`routers/files.py`（DELETE + 按引用检查）、`routers/audit.py`（export CSV）、
  `routers/jobs.py`（export CSV）
- Tests：tests/server/test_file_delete.py、tests/server/test_csv_export.py

### Task 4（主会话）: 挂载/回归/文档/服务说明-V3.3/修改记录
