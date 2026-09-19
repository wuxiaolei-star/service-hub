# Service Hub V3.2 日常效率设计（重跑、筛选分页、进度条、回调重放、日志增强）

- 日期：2026-09-14
- 状态：已确认（按用户授权自主推进）
- 依据：三视角推演报告共识项（功能#1/#2/#4 + UX#1/#2/#3/#4/#5/#7）
- 上游基线：V3.1（`feature/v2-auth-rbac-audit`）

## 1. 目标

消除日常使用中频率最高的五个操作摩擦点。零迁移、零协议破坏。

## 2. 设计

### 2.1 Job 重跑
- `POST /api/v1/jobs/{job_key}/rerun`（operator+）：读取原 Job 的
  `params_json/inputs_json/plugin.version/runtime_type`，走 JobService.create 全流程
  （含配额、Build 校验、审计 action=`job.rerun`，detail.replayed_from=原 job_key）；
  原 Job 非终态 → 409 `JOB_NOT_TERMINAL`；原插件/版本无 ENABLED Build → 既有
  错误透传；
- `JobResponse` 增 `replayed_from: str | None`（只增不改）；
- 前端：任务详情页终态后显示"按原参数重跑"按钮（→ 新详情页）+ 列表页失败行快捷
  重跑；`JobResponse` 类型加 replayed_from。

### 2.2 任务列表筛选/排序/分页
- `GET /api/v1/jobs` 增查询参数：`status`（可重复）、`plugin_id`、`limit`（1-200，
  默认 100）、`offset`（默认 0）；响应增 `total`（符合条件的总数，含过滤）；
- 语义：offset 分页（SQLite 简单可靠）；总数用同条件 COUNT；
- 前端：JobsPage 增状态 Segmented（全部/运行中/成功/失败）+ 插件 Select 筛选 +
  表格分页（pageSize=20，服务端分页）+ 列排序（创建时间/耗时，前端内存排序当前页）。

### 2.3 进度百分比 UI
- 纯前端：JobDetailPage 从 useJobLogStream/useJobLogs 事件中提取最新
  `{type:"progress", percent}` 的 percent，在状态卡渲染 antd Progress（仅
  PENDING/PREPARING/RUNNING/CANCEL_REQUESTED 时展示）；列表页不加（IO 成本）。

### 2.4 Webhook 回调重放
- `POST /api/v1/jobs/{job_key}/callbacks/{callback_id}/replay`（operator+）：
  state IN (FAILED, EXHAUSTED) → 重置 PENDING、attempts=0、next_attempt_at=NULL，
  scheduler 下轮自动重投；审计 action=`webhook.replay`；
- 前端：回调表 EXHAUSTED/FAILED 行加"重放"按钮。

### 2.5 日志查看器增强
- 日志下载按钮：取全量日志（getJobLogs 循环到 next_cursor=NULL）拼文本
  `<job_key>.log` 下载（复用 saveBlob）；
- ERROR/WARNING 行着色（红/橙前缀色块）；
- 关键字过滤输入（前端内存 contains 过滤）。

## 3. 兼容

- 既有 API 字段零删改；`replayed_from`/`total` 为纯增量；
- 角色矩阵不变（rerun/replay 均 operator+，与 create 一致）。
