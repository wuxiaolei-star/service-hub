# Service Hub V3.1 稳定性与正确性设计

- 日期：2026-09-14
- 状态：已确认（按用户授权自主推进）
- 依据：三视角推演报告（架构/功能/UX，2026-09-14）合成结论
- 上游基线：V3.0（`feature/v2-auth-rbac-audit`）

## 1. 目标

消除三视角推演确认的「必然恶化」与「正确性」问题，不新增用户可见功能：

1. **数据生命周期**：OUTPUT 文件、jobs/ 工作区、audit_logs、sessions、job_callbacks
   五处无界增长收口（唯一会在数周内劣化到不可用的点）；
2. **僵尸 Job reaper**：runner 崩溃后 PREPARING/RUNNING 永久滞留，逐次泄漏并发配额
   （默认 8 个）并卡死 PipelineRun；
3. **正确性修复**：API Key 提权口子（operator 可自造 admin Key）；latest_version
   字符串排序（"9.0">"10.0"）；
4. **SSE 增量读**：每秒全量重读 events.jsonl 改为字节偏移增量读；
5. **快修批**：全局 401 拦截跳登录、metrics 结果 TTL 缓存、compose 日志轮转。

## 2. 设计

### 2.1 Job 级保留（进 cleaner，分批 ≤500）
- `RetentionSettings` 增：`job_retention_days=30`、`audit_retention_days=180`；
- `sweep_terminal_jobs`：终态且 `updated_at < now-job_retention_days` 的 Job，限批：
  ① rmtree `jobs/<job_key>/` 工作区（含 events.jsonl 与输出副本）；② 删 Job 行
  （级联 job_files/job_callbacks，pipeline_run_id SET NULL）；③ 删该 Job 的 OUTPUT
  FileRecord 及磁盘负载（INPUT FileRecord 交由既有 TTL sweeper 收尾）；④ 审计
  `job.retention_delete`；
- `sweep_expired_sessions`：删 `expires_at < now` 的行；`sweep_old_audit_logs`：删
  `at < now-audit_retention_days`；
- 三者全部注册进 scheduler_service 默认任务。

### 2.2 僵尸 reaper（进 scheduler）
- `reap_stale_jobs`：`status IN (PREPARING,RUNNING) AND updated_at < now -
  (timeout_seconds + 600s 宽限)` → 置 TIMED_OUT（error_summary 说明被 reaper 回收）
  + 审计 `job.reap`；PipelineRun 由既有 advance_runs 下轮自动终结。

### 2.3 正确性修复
- API Key 创建：请求角色层级不得超过创建者（`ROLE_ORDER` 比对；api_key actor 上限
  为自身角色）；越权 → 403 `FORBIDDEN`；
- `latest_version` 改为按点分整数元组排序（非法段回退 0）。

### 2.4 SSE 增量读
- logs_stream 生成器内持久打开 `events.jsonl`，维护字节偏移：每轮 seek 到 offset
  只读新增行（行缓冲跨轮保留），避免整文件重读；cursor/终态语义不变。

### 2.5 快修批
- client.ts 响应拦截器：401（排除 /auth/login）→ 清缓存跳 /login；
- metrics.py 渲染结果 30s 进程内缓存；
- compose.yaml 两服务加 `logging: json-file max-size 50m max-file 5`。

## 3. 兼容与验收

- 迁移 0007：无新表——`retention` 新字段走 settings，不需 DB 变更；**无迁移**；
- 单测：retention 五类清理各一 + reaper 状态机 + key 提权拒绝 + 版本排序 + SSE
  增量不重发；回归全量；
- Web：401 拦截测试。
