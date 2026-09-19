# Service Hub V3.2 日常效率实施计划

> 依据：`docs/superpowers/specs/2026-09-14-daily-efficiency-v3.2-design.md`
> 分支：`feature/v2-auth-rbac-audit`。零迁移。Task 1/2 后端并行（文件集不相交）；
> Task 3 Web 并行；Task 4 主会话收尾。

### Task 1（子智能体 A）: Job 重跑 + 列表筛选/分页
- Modify：`routers/jobs.py`（rerun 端点 + list 查询参数 status/plugin_id/limit/offset
  + total）、`schemas.py`（JobResponse 增 replayed_from；JobListResponse 增 total）、
  `services/jobs.py`（list 加过滤/count）
- Tests：tests/server/test_job_rerun.py、tests/server/test_job_list_filters.py

### Task 2（子智能体 B）: Webhook 回调重放
- Modify：`routers/webhooks.py`（POST replay 端点）、`services/webhooks.py`
  （replay_callback：FAILED/EXHAUSTED → PENDING + 审计 webhook.replay）
- Tests：tests/server/test_webhook_replay.py

### Task 3（子智能体 C）: Web 前端全部
- 重跑按钮（详情页 + 列表失败行）；列表筛选 Segmented + Select + 服务端分页；
  详情页 Progress 进度条；回调 EXHAUSTED/FAILED 重放按钮；日志下载 + ERROR/WARN
  着色 + 关键字过滤
- Tests 对应

### Task 4（主会话）: 回归 + 文档 + 服务说明-V3.2 + 修改记录
