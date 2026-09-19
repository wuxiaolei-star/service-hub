# Service Hub V3.4 大文件分片上传与 cron 调度设计

- 日期：2026-09-14
- 状态：已确认（按用户授权自主推进）
- 上游基线：V3.3（`feature/v4-plugin-kernel` / main）

## 1. 分片上传

### 端点

```text
POST /api/v1/files/chunk/init        → {"upload_id": "<uuid>"}，创建临时分片目录
PUT  /api/v1/files/chunk/{id}/{seq}  → 存储单个分片（body = raw bytes）
POST /api/v1/files/chunk/{id}/complete → 合并分片 → FileRecord（复用 FileService）
```

- 分片目录：`<storage.root>/uploads/<upload_id>/chunk_<seq>`
- complete 时校验：所有 seq 0..N-1 存在且连续 → 按序合并 → SHA256 → FileRecord
- operator+ 角色；复用 `QuotaService` 配额校验（合并后总大小）
- 超时未 complete 的 init（>24h）由 cleaner 清理
- Audit action：`file.chunk_upload`

### 前端

- `FilesPage.tsx`：文件 >10MB 自动分片（默认 5MB/片），Progress 显示总进度
- 上传库：`web/src/api/files.ts` 增 `uploadFileChunked(file, onProgress)` 纯函数

### hubctl

`file upload --chunk-size 5MB` 可选参数自动分片。

---

## 2. Cron 调度

### 数据模型

迁移 0011：`schedules` 表增 `cron_expr VARCHAR(64) NULL`、
`missed_run_policy VARCHAR(16) DEFAULT 'skip'`（skip|catch_up|latest）。

`interval_minutes` 与 `cron_expr` 二选一（CHECK 约束）。

### 依赖

新增 `croniter>=3.0,<4` 到 hub-server 依赖。

### 触发逻辑

`trigger_due` 扩展：
- `interval_minutes` 模式：现有逻辑不变
- `cron_expr` 模式：用 croniter 计算 next_run_at，触发后按 missed_run_policy 处理

### API

- `POST /schedules` 接受 `cron_expr` 代替 `interval_minutes`（二选一）
- `GET /schedules` 返回 `cron_expr`（如有）

### 前端

SchedulesPage 新建 Modal 增加调度模式切换（间隔 / cron），cron 模式显示表达式输入框 + 下次运行预览。
