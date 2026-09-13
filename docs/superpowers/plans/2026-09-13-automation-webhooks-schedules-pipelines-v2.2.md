# Service Hub V2.2 自动化闭环实施计划

> 依据：`docs/superpowers/specs/2026-09-13-automation-webhooks-schedules-pipelines-v2.2-design.md`
> 分支：`feature/v2-auth-rbac-audit`（继续堆叠）。Task 1 由主会话完成以消除并行冲突；
> Task 2/3/4 为三个并行子智能体（各自独立 router 文件，不碰 main.py/supervisord/迁移）；
> Task 5 挂载与回归由主会话完成；Task 6 Web 并行子智能体；Task 7 文档收尾。

### Task 1（主会话）: 迁移 0005 + 模型 + scheduler 进程骨架
- models：job_callbacks/schedules/pipelines/pipeline_runs + jobs.pipeline_run_id
- alembic 0005；`deploy/service_hub/scheduler_service.py`（循环调度骨架，
  sweep 回调/调度/管道的空实现由后续任务填充 via `services/automation.py` 注册表）
- supervisord `[program:scheduler]`；healthcheck 五进程

### Task 2（子智能体 A）: Webhook 回调后端
- `routers/webhooks.py`（GET /jobs/{job_key}/callbacks，viewer）
- jobs 创建接受 callback_url/secret → 登记 job_callbacks（改 jobs.py 的 create_job 需主会话协调——**改为**：A 提供函数 `enqueue_callback(session, job_id, url, secret)`，由主会话在 Task 5 统一接线到 create_job）
- `services/webhooks.py`：deliver_due(session, now) → 状态机 + HMAC 签名 + 退避 + 审计
- 测试：tests/server/test_webhooks_api.py、tests/server/test_webhook_delivery.py

### Task 3（子智能体 B）: 定时任务后端
- `routers/schedules.py` CRUD + enable/disable/delete（矩阵见 spec）
- `services/schedules.py`：trigger_due(session, now) → 创建 Job + next_run 推进 + 审计
- 测试：tests/server/test_schedules_api.py、tests/server/test_schedule_trigger.py

### Task 4（子智能体 C）: 线性管道后端
- `routers/pipelines.py` CRUD/execute/runs 查询
- `services/pipelines.py`：start_run(session, pipeline) 创建 run+首步 Job；
  advance_runs(session) 处理终态步进与失败即停（$prev 替换）
- 测试：tests/server/test_pipelines_api.py、tests/server/test_pipeline_advance.py

### Task 5（主会话）: 挂载与接线
- main.py 挂载三个 router；create_job 接线 callback_url；
- scheduler_service 调用 deliver_due/trigger_due/advance_runs；全量回归 + 修复

### Task 6（子智能体 D）: Web 管理台
- 定时任务管理页（列表/新建/启停/删除）、任务详情页回调投递状态卡、
  管道页（列表/执行/运行进度）；菜单与路由；全部 mock 测试

### Task 7（主会话）: 文档、验收、服务说明 V2.2、修改记录
