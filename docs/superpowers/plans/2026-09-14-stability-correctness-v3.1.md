# Service Hub V3.1 稳定性与正确性实施计划

> 依据：`docs/superpowers/specs/2026-09-14-stability-correctness-v3.1-design.md`
> 分支：`feature/v2-auth-rbac-audit`。零迁移。三个子智能体并行（文件集不相交）；
> Task 4 主会话收尾。

### Task 1（子智能体 A）: 数据生命周期 + 僵尸 reaper
- Modify：`settings.py`（RetentionSettings 增 job_retention_days/audit_retention_days）、
  `services/retention.py`（sweep_terminal_jobs/sweep_expired_sessions/sweep_old_audit_logs）、
  新 `services/reaper.py`（reap_stale_jobs）、`scheduler_service.py`（注册三个新任务）
- Tests：tests/server/test_retention.py 扩展、tests/server/test_reaper.py

### Task 2（子智能体 B）: 正确性修复
- Modify：`routers/users.py`（Key 创建角色钳制）、`routers/plugins.py`（latest_version
  点分整数排序）
- Tests：tests/server/test_key_privilege.py、tests/server/test_version_sort.py

### Task 3（子智能体 C）: SSE 增量读 + 全局 401
- Modify：`routers/logs_stream.py`（字节偏移增量读，语义不变）、
  `web/src/api/client.ts`（401 拦截跳登录）+ 对应测试

### Task 4（主会话）: metrics 缓存、compose 日志轮转、全量回归、
  文档（验收报告 V3.1 节）、`服务说明-V3.1.md`、修改记录
