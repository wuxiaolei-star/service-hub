# Service Hub V3.3 治理完善设计

- 日期：2026-09-14
- 状态：已确认（按用户授权自主推进）
- 上游基线：V3.2（`feature/v2-auth-rbac-audit`）

## 1. 目标

补齐三视角推演剩余的治理项，全部 S/M 级：备份空间预检、webhook 投递明细表、
文件删除自助 + CSV 导出、服务 reconcile。零 API 破坏。

## 2. 设计

### 2.1 备份空间预检（S）
- `services/backup.py` 的 `create_backup` 前置校验：`shutil.disk_usage(storage_root).free`
  < 数据目录大小 × 1.3 → 409 `DISK_SPACE_INSUFFICIENT`（detail 含 free/needed）。

### 2.2 Webhook 投递明细表（M）
- 迁移 0009 新表 `webhook_deliveries`（id, callback_id FK CASCADE, attempted_at,
  status_code, ok BOOLEAN, error TEXT）；`_deliver_one` 每次尝试写入一行；
- `GET /jobs/{key}/callbacks/{callback_id}/deliveries`（viewer）：该回调的逐次投递
  历史（时间倒序）。

### 2.3 文件删除自助（S）
- `DELETE /api/v1/files/{file_key}`（operator+）：校验无任何 job_files 引用
  （RESTRICT 会阻止——先查有引用则 409 `FILE_IN_USE`）→ unlink 负载 → 删记录 +
  审计 `file.delete`。

### 2.4 CSV 导出（S）
- `GET /api/v1/audit-logs/export`（admin，text/csv）：全部审计（按时间升序）
  流式输出；列：at,actor_type,actor_name,action,resource_type,resource_id,result,ip；
- `GET /api/v1/jobs/export`（operator+，text/csv）：最近 10000 条 Job；列：
  job_id,plugin_id,version,runtime_type,status,created_at,started_at,finished_at,
  error_summary。

### 2.5 服务 reconcile（S）
- scheduler 增 `reconcile_services(session, settings)` 任务（每轮调）：遍历
  desired_state=RUNNING 的 ServiceDef → 调 client.status → 容器不存在/非 running
  → client.start + 审计 `service.reconcile`（result=ok/denied）。

## 3. 迁移

- 0009：`webhook_deliveries` 表（仅新表）。
