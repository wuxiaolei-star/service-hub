# Service Hub V2.3 平台体验设计（插件仓库、秒传查重、SSE 实时日志）

- 日期：2026-09-14
- 状态：已确认（按用户授权自主推进）
- 依据：`docs/design/路线图-V2.0至V3.0.md` V2.3 章节
- 上游基线：V2.2（`feature/v2-auth-rbac-audit`）

## 1. 目标

1. **插件仓库**：Hub 内已安装的插件 Build 成为可查询、可下载的 registry，
   hubctl 一条命令在多台 Hub 间同步插件；
2. **秒传查重**：上传前按 SHA256 查询已有文件，命中则跳过传输；
3. **SSE 实时日志**：任务详情页日志从 2 秒轮询升级为 Server-Sent Events 准实时推送。

分片/断点续传上传**延后**（纳入 backlog，见 §6）。

## 2. 行为规格

### 2.1 插件仓库
- `GET /api/v1/registry/plugins`（viewer）：全部插件 → 版本 → Build（build_id、
  runtime、arch、status、package_sha256、文件大小）；
- `GET /api/v1/registry/download/{build_key}`（publisher+）：流式返回对应 `.pypkg`
  （Content-Disposition 附件名 `插件-build.pypkg`）；READY/ENABLED/FAILED 均可下载
  （FAILED 包仍在盘，便于排查）；非 404 `PLUGIN_BUILD_NOT_FOUND`；
- 审计：下载动作写审计（action=`registry.download`）；
- hubctl：`registry list`、`registry pull <build_key> --output <目录>`（下载到本地）、
  `registry sync --output <目录>`（全量拉取所有 ENABLED 包，逐个打印 SHA256）。

### 2.2 秒传查重
- `GET /api/v1/files/by-sha256/{sha256}`（operator+）：返回该哈希最新一个
  AVAILABLE 文件的元数据（FileResponse）；无 → 404 `FILE_NOT_FOUND`；
- 用途：上传前先查重，命中则直接用既有 file_id，跳过传输（管理台与业务系统均可）；
- 完整性不变：查询不产生新记录，文件内容以既有记录为准。

### 2.3 SSE 实时日志
- `GET /api/v1/jobs/{job_key}/logs/stream`（viewer）：`text/event-stream`；
  服务端复用 JobService.logs 游标逻辑，从 cursor=0 增量推送，1 秒间隔轮询文件，
  Job 终态且日志读尽后发送 `event: end` 并关闭；客户端断开自动停止；
- 事件格式：`event: log\ndata: <json 单行>\n\n`；`event: end\ndata: {}\n\n`；
- Web 任务详情页：Job 活动态改用 EventSource（保留原轮询作为 SSE 不可用时的降级）。

## 3. 安全与兼容

- 全部端点走既有角色矩阵；SSE 流经 Nginx（`proxy_buffering off` 已具备，SSE 兼容）；
- registry 下载是既有 build 包文件的只读暴露，无新数据面；
- 迁移：无（零新表）。

## 4. 测试与验收

- 单元：registry 列表/下载/审计、查重命中与 404、SSE 事件序列（含 end）与权限；
- 集成：hubctl registry pull 下载包 SHA256 与管理台一致、SSE 在真实部署可连。
