# Service Hub V3.4 分片上传 + cron 调度实施计划

> 依据：`docs/superpowers/specs/2026-09-14-chunked-upload-cron-v3.4-design.md`
> 分支：`feature/v4-plugin-kernel`（继续堆叠）

### Task 1（子智能体 A）: 分片上传后端
- Modify `routers/files.py`（chunk-init/chunk-upload/chunk-complete 三端点）、`services/files.py`（分片合并逻辑）
- Tests：tests/server/test_chunk_upload.py

### Task 2（子智能体 B）: cron 调度后端
- Modify `models.py`（Schedule 增 cron_expr/missed_run_policy）、`alembic/versions/0011_*.py`、
  `services/schedules.py`（croniter 计算 next_run_at）、`routers/schedules.py`（接受 cron_expr）
- 增依赖 `croniter>=3.0,<4`
- Tests：tests/server/test_cron_schedule.py

### Task 3（子智能体 C）: Web 前端
- Modify `web/src/api/files.ts`（uploadFileChunked）、`web/src/pages/FilesPage.tsx`（大文件自动分片+进度）、
  `web/src/pages/SchedulesPage.tsx`（cron 表达式模式）
- Tests 对应

### Task 4（主会话）: hubctl 分片支持、回归、文档、服务说明-V3.4
