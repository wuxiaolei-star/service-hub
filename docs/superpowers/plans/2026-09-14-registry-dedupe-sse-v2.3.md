# Service Hub V2.3 平台体验实施计划

> 依据：`docs/superpowers/specs/2026-09-14-registry-dedupe-sse-v2.3-design.md`
> 分支：`feature/v2-auth-rbac-audit`。零迁移。Task 1/2 并行子智能体；
> Task 3 Web 子智能体；Task 4 主会话挂载/回归/文档。

### Task 1（子智能体 A）: 插件仓库 + 秒传查重后端
- Create `routers/registry.py`（GET /registry/plugins viewer；
  GET /registry/download/{build_key} publisher+，StreamingResponse，审计）；
- Modify `routers/files.py` 增 `GET /files/by-sha256/{sha256}`（operator+）；
- Modify `tools/hubctl` 增 `registry list|pull|sync`；
- Tests：tests/server/test_registry_api.py、tests/tools/test_hubctl_registry.py

### Task 2（子智能体 B）: SSE 日志流后端
- Create `routers/logs_stream.py`（GET /jobs/{job_key}/logs/stream，viewer；
  StreamingResponse text/event-stream，1s 轮询游标增量，终态读尽发 `event: end`）；
- Tests：tests/server/test_logs_stream_api.py

### Task 3（子智能体 C）: Web 前端
- 插件详情抽屉/插件页增加"从仓库下载"入口（或独立 RegistryPage）；
- JobDetailPage 日志改 EventSource（降级轮询）；
- Tests 对应

### Task 4（主会话）: 挂载 registry/logs_stream 路由、全量回归、
  文档（指南/README/验收报告 V2.3 节）、服务说明-V2.3、修改记录

## 已知取舍
- 分片/断点续传上传延后（backlog），本版交付秒传查重；
- SSE 服务端轮询文件而非进程内推送——单机 SQLite 形态下足够，且与既有 logs 端点语义一致。
