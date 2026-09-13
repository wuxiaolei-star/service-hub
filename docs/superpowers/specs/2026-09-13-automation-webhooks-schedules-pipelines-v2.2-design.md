# Service Hub V2.2 自动化闭环设计（Webhook 回调、定时任务、线性管道）

- 日期：2026-09-13
- 状态：已确认（按用户授权自主推进）
- 依据：`docs/design/路线图-V2.0至V3.0.md` V2.2 章节
- 上游基线：V2.1（`feature/v2-auth-rbac-audit`）

## 1. 目标

把"人守着页面点"升级为"链路自动跑"：

1. **Webhook 回调**：Job 终态时向业务系统 POST 签名通知，带重试与投递记录；
2. **定时任务**：按分钟间隔周期性自动创建 Job（如每日数据更新）；
3. **线性管道**：前一步 Job 的输出文件自动作为下一步输入，失败即停。

本轮不实现：DAG 分支/汇聚、多目标广播、事件总线、分布式调度。

## 2. 架构决策

- 新增 supervisord 第五受管进程 **`scheduler`**（hub-api 用户），统一承担三类异步工作：
  定时任务触发、Webhook 投递重试、管道步进；healthcheck 必需清单同步扩为五进程；
- 触发/投递/步进全部**落库状态机**（进程崩溃重启后可续），不依赖内存态；
- 迁移 0005 一次交付三组表；三个后端特性各自独立 router 文件，由主会话统一挂载，
  避免并行实施时 main.py 冲突。

## 3. 数据模型（迁移 0005）

~~~text
job_callbacks   id, job_id(FK jobs CASCADE), url, secret, state(PENDING|SUCCEEDED|
                FAILED|EXHAUSTED), attempts, last_status_code, last_error,
                next_attempt_at, created_at, updated_at
schedules       id, name UNIQUE, plugin_id, version, runtime_type, inputs_json,
                params_json, interval_minutes, enabled, next_run_at, last_job_id,
                created_at, updated_at
pipelines       id, name UNIQUE, steps_json, created_at, updated_at
                steps_json: [{plugin_id, version, runtime_type,
                              inputs: {输入名: "file_<id>" | "$prev.<输出名>"},
                              params: {}}]   （≥1 步）
pipeline_runs   id, pipeline_id(FK CASCADE), state(PENDING|RUNNING|SUCCEEDED|FAILED),
                current_step, created_at, updated_at
jobs            增列 pipeline_run_id（FK pipeline_runs SET NULL, 可空）
~~~

## 4. 行为规格

### 4.1 Webhook 回调
- `POST /jobs` 请求体可选 `callback: {"url": "https://...", "secret": "可选"}`；
  创建 Job 时同步登记 `job_callbacks(state=PENDING)`；
- scheduler 每 30s 扫描 `state IN (PENDING, FAILED) AND next_attempt_at <= now` 的
  Job 所属任务已终态的记录：POST url，body 为该 Job 的公开响应 JSON，头
  `X-Hub-Signature: sha256=<HMAC-SHA256(body, secret)>`（无 secret 则不带头）；
  2xx → SUCCEEDED；否则 attempts+1，最多 3 次，退避 60s×attempts，耗尽 → EXHAUSTED；
- `GET /api/v1/jobs/{job_key}/callbacks`（viewer）返回该 Job 的投递记录；
- 每次投递写审计（action=`webhook.deliver`，result=ok/denied）。

### 4.2 定时任务
- CRUD：`POST/GET /api/v1/schedules`（operator+；body: name/plugin_id/version/
  runtime_type/inputs/params/interval_minutes）、`POST /{id}/enable|disable`、
  `DELETE /{id}`（admin 删，operator+ 停启用）；
- scheduler 扫描 `enabled=true AND next_run_at <= now`：按配置创建 Job（owner=NULL，
  审计 action=`schedule.trigger`），`next_run_at += interval`，记录 `last_job_id`；
  触发失败不影响下轮；
- Job 响应体新增只读字段？——不加，保持既有字段；关联通过审计与
  `GET /schedules`（含 last_job_id）追溯。

### 4.3 线性管道
- `POST /api/v1/pipelines`（operator+，steps ≥1）；`GET /pipelines`、`GET /pipelines/{id}`；
  `POST /pipelines/{id}/execute`（operator+）→ 创建 pipeline_runs(RUNNING, step=0) 并
  创建第 0 步 Job（inputs 中 `$prev.*` 在首步非法，直接 422）；
- scheduler 扫描 RUNNING run 的当前步 Job：SUCCESS → 取该 Job 输出 file_id（按输出
  logical_name），把下一步 inputs 中 `$prev.<输出名>` 替换为实际 file_id 后创建下一
  Job；无下一步 → run=SUCCEEDED；当前步 FAILED/CANCELLED/TIMED_OUT → run=FAILED；
- 管道创建的 Job 写审计（action=`pipeline.step`，detail 含 run/step）；
- `GET /api/v1/pipeline-runs?pipeline_id=`（viewer）查看进度。

## 5. 角色矩阵增量

| 端点 | 最低角色 |
| --- | --- |
| `GET /jobs/{id}/callbacks`、`GET /pipeline-runs*` | viewer |
| `POST /schedules*`、`POST /pipelines*`、`POST /pipelines/{id}/execute` | operator |
| `DELETE /schedules/{id}` | admin |

## 6. 测试与验收

- 单元：回调状态机（2xx/非2xx/耗尽/签名）、调度触发与 next_run 推进、管道 `$prev`
  替换与失败即停（用假 JobService 或 API 级 seed build）；端点角色矩阵增量；
- 集成：真机验证 scheduler 进程 RUNNING、定时触发产生 Job、回调收到签名请求
  （本地 HTTP stub）、两步管道自动步进。
