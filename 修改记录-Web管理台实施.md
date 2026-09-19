# Web 管理台实施修改记录

> 分支：`feature/service-hub-web-console-impl`（工作树 `.worktrees/service-hub-web-console-impl`）
> 依据计划：`docs/superpowers/plans/2026-09-13-service-hub-web-console.md`（共 12 个任务）
> 本文随实施过程同步维护。Task 1–3 由先前会话完成；Task 4 由并行会话完成，本文验收记录。

---

## Task 4：共享组件（并行会话完成，提交 c3383bd + 84a5fdf，本次验收）

- `web/src/components/StatusTag.tsx`：集中状态→中文标签/颜色映射，未知状态显式显示"未知状态"。
- `web/src/components/HubErrorAlert.tsx`：错误消息+code 可见，详情折叠（Collapse），可选重试按钮。
- `web/src/components/UploadPanel.tsx`：antd Upload.Dragger 上传面板 + 进度条。
- `web/src/components/PageHeader.tsx`、`EmptyState.tsx`：页头与空态。
- `web/src/utils/download.ts`：`safeDownloadName`（拒绝路径穿越/分隔符/控制字符/空名，扩展名去重）与 `saveBlob`（object URL 在 finally 中释放，click 异常继续抛出但仍清理 anchor）。
- `web/src/utils/format.ts`：`formatFileSize`、`formatDateTime`。

验收：`npm run test:run -- src/components src/utils src/api src/hooks` → 80 passed。

## Task 5：概览与系统页面（接手并行会话 RED 后完成，提交 1d46b51）

接手时工作树内有未提交的 Dashboard/System 页面与测试（RED 状态），本次完成 GREEN：

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `web/src/routes/router.tsx` | 修改 | index → `DashboardPage`，`/system` → `SystemPage`；修复被误删的 antd `Card/Typography` 导入（导致 `placeholderPage` ReferenceError） |
| `web/src/layouts/AppLayout.tsx` | 修改 | 顶栏接入 `getHealth` 10 秒轮询：UP→服务正常(success)、错误→服务离线(error)、加载中→状态未知(default) |
| `web/src/test/setup.ts` | 修改 | jsdom 补 `window.matchMedia` 与 `ResizeObserver` mock（antd 响应式栅格必需，此前导致全部页面测试空渲染失败） |
| `web/src/layouts/AppLayout.test.tsx` | 修改 | index 路由改为数据页后：mock 三个 API 模块、"概览"断言收窄到导航内、等待"已注册插件"渲染后再断言卡片样式 |
| `web/src/pages/DashboardPage.tsx` + `.test.tsx` | 新增（接手） | 统计卡片（已注册插件/运行环境 Build/可用 Build 明细/进行中任务）+ 最近任务表（明确标注最多 100 条）+ 加载骨架 + 失败重试 |
| `web/src/pages/SystemPage.tsx` + `.test.tsx` | 新增（接手） | 只读系统信息 + V1 无认证安全警告 |

验证：89 前端测试全绿（本轮 85），typecheck/lint/build 通过。

提交信息：`feat(web): add dashboard and system status`

## Task 6：插件管理页（提交 ebc0714）

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `web/src/hooks/usePluginInstall.ts` | 新增 | 安装 `.pypkg` → 上传进度 → 轮询 Build（`pollWhileBuildActive`，仅 INSTALLING 时 2s）→ 终态后失效 Build 列表缓存；上传响应本身不决定最终状态 |
| `web/src/hooks/useBuildToggle.ts` | 新增 | 共享 Build 启用/停用 mutation；响应写入 Build 详情缓存（setQueryData）再失效列表 |
| `web/src/pages/PluginsPage.tsx` | 新增 | 插件表格 + 上传面板；最近安装 Build 就地显示状态并可直接启用/停用（Popconfirm）；FAILED 显示 error_summary；错误统一走 `toHubApiError` |
| `web/src/pages/PluginDetailDrawer.tsx` | 新增 | 抽屉展示入口/运行时/参数表/Build 表；READY→启用，ENABLED→Popconfirm 停用 |
| `web/src/api/errors.ts` | 修改 | `toHubApiError` 透传已是 HubApiError 形态的拒绝对象（axios 检查在前，避免误吞 AxiosError 的响应体解析） |
| `web/src/pages/PluginsPage.test.tsx` | 新增 | 4 条工作流：安装→轮询 READY→启用；FAILED 显示错误摘要；抽屉参数+停用（确认弹层）；安装失败错误展示 |

实施要点：antd 两字按钮自动插空格（可访问名"启 用"/"停 用"），选择器用 `/启\s*用/`；antd 动画致抽屉用例约 5s，显式 15s 超时；抽屉内容断言用 `findBy*` 避免加载期时序抖动。

验证：连续 3 轮 89 tests passed；typecheck/lint/build 通过。

提交信息：`feat(web): add plugin build management`

## Task 7：文件管理页与最近文件持久化（提交 ec2d8b7）

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `web/src/hooks/useRecentFiles.ts` | 新增 | localStorage 持久化（键 `service-hub.recent-files.v1`）最近 50 条文件元数据；按 file_id 去重、损坏 JSON 与非法条目忽略、只存公开元数据（无 blob/token）；提供 recordUpload/forgetRecord/lookup |
| `web/src/pages/FilesPage.tsx` | 新增 | 上传面板（进度/失败告警）+ 服务器最近 100 条与本地记录合并展示（标注来源）+ 按 ID 查找（查找失败保留本地记录并提示）+ 复制文件 ID + 安全下载（文件名经 `safeDownloadName`；元数据获取失败时用行内名称回退，不阻断 blob 下载） |
| `web/src/routes/router.tsx` | 修改 | `/files` → `FilesPage` |
| `web/src/hooks/useRecentFiles.test.ts` | 新增 | 6 条：仅存元数据且去重、50 条上限、损坏 JSON 忽略、非法条目过滤、forgetRecord、lookup |
| `web/src/pages/FilesPage.test.tsx` | 新增 | 4 条工作流：上传进度+列表、服务器没有的 ID 从最近记录查找、查找失败保留记录、安全文件名下载 |

实施要点：`URL.createObjectURL` 在 jsdom 中不存在，用 `vi.stubGlobal` 模拟；下载链路对元数据失败做了容错（先取 blob 再尽力取名字）。

验证：连续 2 轮 99 tests passed；typecheck/lint/build 通过。

提交信息：`feat(web): add file upload and download management`

## Task 8：Manifest 驱动的新建 Job（提交 93104dd）

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `web/src/utils/jobPayload.ts` | 新增 | `validateJobRequest`（必填/默认值/min/max/enum 选项/boolean/string_list 去重/不支持类型阻断/多文件 min_count/缺失运行时）、`buildJobRequest`（canonical 请求对象，含默认值回填）、`parseJsonRequest`（JSON 模式校验） |
| `web/src/components/PluginParameterForm.tsx` | 新增 | 按参数类型渲染控件（string→Input、integer/number→InputNumber、boolean→Switch、enum→Select、string_list→tags Select），带错误提示与必填星标 |
| `web/src/pages/NewJobPage.tsx` | 新增 | 插件/版本选择 → 只列出 ENABLED Build 并据此约束运行时（单运行时自动锁定）→ 输入文件（可从最近文件选或手输 ID，files 类型逗号分隔多选）→ 表单/JSON 双模式共享请求预览（data-testid=request-preview）→ 校验失败阻止提交、成功跳转 `/jobs/{job_id}` |
| `web/src/routes/router.tsx` | 修改 | `/jobs/new` → `NewJobPage` |
| `web/src/utils/jobPayload.test.ts` | 新增 | 9 条：精确 NC payload、必填校验、min/max、enum/去重、不支持类型、多文件 min_count、缺失运行时、JSON 解析 |
| `web/src/pages/NewJobPage.test.tsx` | 新增 | 4 条工作流：默认值+运行时锁定+预览、提交 canonical 请求并跳转、缺必填阻止提交、JSON 模式实时校验与预览 |

实施要点：antd 两字按钮/Radio 的可访问名带空格或 input `pointer-events:none`，点击用标签文本；表单默认值通过 manifest 变化时的 effect 初始化；JSON 解析错误实时显示（不依赖提交）。

验证：112 tests passed（两轮）；typecheck/lint/build 通过。

提交信息：`feat(web): add manifest driven job creation`

## Task 9：Job 中心/详情/日志/取消/输出（提交 64050aa）

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `web/src/utils/jobTime.ts` | 新增 | `formatDuration`：排队计时（创建起）、运行计时（开始起）、结束计时（开始→结束，无开始回退创建）、非法/缺失时间显示 `-`、小时格式 |
| `web/src/hooks/useJobLogs.tsx`（原 `.ts`，含 JSX fixture 改名） | 新增 | 游标累加日志分页：按页签名去重（同页重复拉取不重复追加）、`active` 时 2s 轮询、拉平后 `exhausted` 停止、active=false 时拉平即停、卸载经 TanStack 取消 |
| `web/src/components/JobLogViewer.tsx` | 新增 | `<pre>` 文本渲染（进度/级别前缀），底部跟随自动滚动，向上滚动暂停并显示"恢复自动滚动" |
| `web/src/pages/JobsPage.tsx` | 新增 | 任务表（最近 100 条）：状态/创建时间/耗时列，存在活动任务时 2s 刷新，行操作跳转详情 |
| `web/src/pages/JobDetailPage.tsx` | 新增 | 详情卡片（状态/耗时/Build/起止时间）、失败摘要 Alert、运行中可确认取消、RUNNING 时轮询；输出仅在 SUCCESS 后查询并支持安全命名下载 |
| `web/src/routes/router.tsx` | 修改 | `/jobs`、`/jobs/:jobId` 接入实际页面；删除全部 placeholder |
| 测试 | 新增 | `jobTime.test.ts`（6 条）、`useJobLogs.test.tsx`（4 条：有序累加/活动轮询/重复抑制/卸载停止）、`JobsPage.test.tsx`（2 条）、`JobDetailPage.test.tsx`（4 条：运行轮询+日志、失败摘要、确认取消、SUCCESS 后才取输出+安全下载） |

实施要点：JobDetailPage 测试需同时 mock `useParams` 与 `useNavigate`（页面在 Router 上下文外渲染）；日志行带 `[INFO]` 前缀，断言用正则；`useJobLogs.test` 因 fixture 使用 JSX 由 `.ts` 改为 `.tsx`。

验证：两轮 128 tests passed；typecheck/lint/build 通过。

提交信息：`feat(web): add job monitoring and outputs`

## Task 10：Nginx + Compose 打包 Web 镜像（提交 d4b1814）

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `web/Dockerfile` | 新增 | 两阶段：`node:22-alpine`（npm ci + build）→ `nginx:1.27-alpine` 只复制 dist 与配置；EXPOSE 8080；wget HEALTHCHECK `/web-health` |
| `web/nginx.conf` | 新增 | `listen 8080`；`/api/v1/` 代理到 `http://service-hub:8000/api/v1/`（关闭缓冲，10g 体积，3700s 读写超时）；`/internal/v1/` 返回 404；`/` SPA fallback `try_files $uri $uri/ /index.html`；`/web-health` 200 |
| `web/.dockerignore` | 新增 | node_modules/dist/.git 不进构建上下文 |
| `compose.yaml` | 修改 | 新增 `service-hub-web` 服务（image/platform/restart/`127.0.0.1:8080:8080`，无任何挂载，`depends_on: service-hub: service_healthy`） |
| `tests/deploy/test_web_container_files.py` | 新增 | Compose 双服务静态断言、多阶段 Dockerfile、Nginx 安全路由断言 |
| `tests/server/test_container_smoke.py` | 修改 | 生成器与集成冒烟改为双服务拓扑；集成用例增加 Web 首页/代理 health/SPA 刷新/internal 404 四项验证 |

提交信息：`feat(deploy): serve web console through nginx`

## Task 11：离线发布包扩展 Web 镜像（提交 e34145f + 28a2d25）

- `deploy/release/build-release.sh`：新增 `WEB_IMAGE_NAME`，构建并保存 `service-hub-web-image.tar`，SHA256SUMS 纳入双镜像。
- `deploy/release/install.sh`：`docker load -i service-hub-web-image.tar`。
- `deploy/release/status.sh`：新增 8080 端口 web-proxied health 检查（失败也退出 1）。
- `tests/deploy/test_release_scripts.py`：fixture 增加伪造 web 镜像 tar 与校验行、docker stub 支持双 load；build/install/status 断言更新。
- 随后修复 2 个 runner 测试对新双服务 Compose 的过时断言（28a2d25）。

验证：`bash -n` 全部通过；`pytest -m "not integration"` 499 passed。

提交信息：`feat(release): bundle service hub web console`、`fix(runner): accept web service in compose assertions`

## Task 12：文档与验收（提交 358830e）

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `docs/guides/Web管理台构建部署与使用.md` | 新增 | 中文操作指南：本地双镜像构建（Docker Desktop/PowerShell+Bash）、save/load 离线搬运、`HUB_HOST_DATA_DIR` + 一条 Compose 命令启动、四入口健康验证、SSH 隧道、运维命令、页面↔API 对照表、hubctl 等价用法、Nginx 边界、备份/升级/回滚（禁止 down -v）、常见错误表 |
| `README.md` | 修改 | 目录表新增 web/；快速部署增加 Web 镜像构建与 8080 说明；入口链接指向 Web 指南 |
| `docs/guides/单容器部署与脚本插件使用.md` | 修改 | Nginx 边界章节交叉引用 Web 指南与 service-hub-web 服务 |
| `docs/reports/single-container-linux-amd64-acceptance.md` | 修改 | 新增"Web 管理台验收"章节（9 项待填字段）与质量门更新 |
| `tests/deploy/test_documented_commands.py` | 修改 | 新增 4 条一致性测试：README 双镜像+8080+新指南链接；Web 指南覆盖构建/导入/Compose/边界/运维；工作流关键词；单容器指南交叉链接 |
| `tests/integration/test_web_console_lifecycle.py` | 新增 | 集成验收：一次性 Compose 双服务栈，验证 SPA 首页、SPA 刷新、代理 health、internal 404、Web 容器无 /data 与 docker.sock 挂载、上传 201、restart 后数据持久化 |

验证（开发机全部门禁）：

- `python -m pytest -m "not integration"`：499 passed
- `python -m ruff check .`：通过；`python -m mypy packages`：53 文件无问题
- `web`：vitest 128 passed、typecheck/lint/build 通过（node_modules 曾因 npm 缓存目录不可写损坏，用本地缓存 `--cache .npm-cache` 重新 `npm ci` 修复）
- Linux 侧验收项（真实镜像构建、集成测试、发布包）待原生 Linux AMD64 验收机执行并回填报告

提交信息：`docs: add web console deployment and acceptance`

## Final branch gate 状态

- 本地可执行项（python 全量非集成、ruff、mypy、web 全套、compose config、git diff --check）全部通过。
- Linux AMD64 侧（双镜像构建、`test_web_console_lifecycle.py`、NC 双运行时集成、离线发布包制作与 SHA256 校验）待验收机执行；报告"未通过项"为空后分支才可进入 PR 评审合并 main。

---

# V2.0 认证、RBAC 与审计（分支 feature/v2-auth-rbac-audit，2026-09-13）

依据：`docs/superpowers/specs/2026-09-13-auth-rbac-audit-v2.0-design.md` 与
`docs/superpowers/plans/2026-09-13-auth-rbac-audit-v2.0.md`（提交于 feature/service-hub-web-console 分支）。

| 任务 | 提交 | 内容 |
| --- | --- | --- |
| Task 1 数据模型与迁移 | 3693462 | models.py 新增 User/Session/ApiKey/AuditLog 四模型（role CHECK 约束、sessions 级联删除与 24h 默认过期）；迁移 0003 建四表 |
| Task 2 认证核心 | 27f5351 | services/auth.py：argon2 口令哈希、256 位 token/`hub_` 前缀 API Key 生成、SHA256 存储校验、会话/Key 生命周期与吊销；AuthSettings（mode/session_ttl_hours，extra=forbid）+ `HUB_AUTH_MODE` env 覆盖 + YAML 布尔 `off` 规范化 |
| Task 3 角色矩阵 | 9b6cd0b | dependencies_auth.py：Cookie/Bearer 双通道解析、Actor 数据类、`require_role` 四级角色、401/403 错误、off 模式匿名直通；files/plugins/jobs/system 逐端点应用矩阵 |
| Task 4 Auth 端点与引导 | b56cd0c | /auth/login|logout|me|change-password；首启引导 admin 写数据目录 bootstrap-admin.json（0600）+ must_change_password 标志；Cookie HttpOnly/SameSite=Lax/Secure 跟随 scheme |
| Task 5 用户与 Key 管理 | 883a1d3 | /users CRUD（admin，停用/一次性重置口令）、/api-keys 签发（明文只显示一次）/吊销；Bearer 优先于 Cookie 的凭证解析 |
| Task 6 审计 | fda06a1 | services/audit.py 打点 helper；覆盖登录成功/失败、登出、改密、上传/下载、安装/启停 Build、建/取消 Job、用户与 Key 管理；GET /audit-logs（admin，action/actor 过滤） |
| Task 7 hubctl | 6898c9e | login/logout/whoami 子命令；HUB_TOKEN env 优先、~/.hubctl/credentials(0600) 兜底；401 输出重新登录提示 |
| Task 8 Web 界面 | f13273f | 登录页、RequireAuth 路由守卫、顶栏身份+登出、用户管理/API Key（一次性明文展示）/审计页、/admin/* 路由 |
| Task 9 文档验收 | 本次提交 | hub.yaml.example 增加 auth 节；单容器指南认证章节；README V2.0 说明；验收报告 V2.0 章节；test_auth_lifecycle 集成测试 |

最终质量门（开发机）：python 非集成 563 passed；ruff/mypy 通过；compose config 通过；
web 131 passed + typecheck/lint/build 通过。

待 Linux AMD64 验收项已列入 `docs/reports/single-container-linux-amd64-acceptance.md` 第 8 节。

---

# V2.1 资源治理与可观测（2026-09-13，设计与计划 f6ff388）

| 任务 | 提交 | 内容 |
| --- | --- | --- |
| Task 1 配置与模型 | fc4cf29 | QuotasSettings/RetentionSettings（strict）；users 三配额覆盖列；file_records/jobs.owner_user_id（迁移 0004，SQLite batch+命名外键） |
| Task 2 配额强制 | 58a0118 | services/quotas.py：上传（预检 Content-Length + 落盘精确复核排除自身）与建 Job（用户非终态计数）双执行点；409 QUOTA_EXCEEDED 含 used/limit；admin/匿名豁免、开关直通 |
| Task 3 用量端点 | 248188b | GET /users/{id}/usage（admin）：total_bytes/file_count/active_jobs |
| Task 4 清理循环 | d2d39c1 | services/retention.py（INPUT+超 TTL+无引用→删除，含磁盘负载与审计，每轮≤500）；deploy cleaner 进程；supervisord 四进程 + healthcheck 同步 |
| Task 5 指标端点 | bc0ecca | services/metrics.py 手写 Prometheus 文本；GET /system/metrics（admin） |
| Task 6 备份 | 46cbfff | services/backup.py（SQLite backup API 在线快照+tar.gz 原子落盘+保留 3 份修剪）；POST /system/backup（admin+审计）；hubctl backup create/restore |
| Task 7 Web 展示 | 8f81e6f | 用户页用量列（逐行懒加载）；系统页运行指标卡（15s 轮询，403 隐藏）；parseHubMetrics 解析器单测 |
| Task 8 文档验收 | 71d7d07 系列 | hub.yaml.example quotas/retention 节；指南"配额、清理与备份"章；验收报告 V2.1 节；集成 test_quota_lifecycle.py；服务说明-V2.1.md |

Task 6/7 由两个并行子智能体实施（文件集不相交，主会话统一验收提交）。
最终质量门：python 非集成 599 passed；web 139 passed + typecheck/lint/build；mypy strict 63 文件无问题。
服务说明：`docs/service-docs/服务说明-V2.1.md`。

---

# V2.2 自动化闭环（2026-09-13，spec/plan 6bf3551）

| 任务 | 提交 | 内容 |
| --- | --- | --- |
| Task 1 模型/迁移/scheduler | bb91a61+ee0f593 | 迁移 0005（job_callbacks/schedules/pipelines/pipeline_runs + jobs.pipeline_run_id）；supervisord 第五进程 scheduler；FileRecord 列丢失事故修复 |
| Task 3 定时任务 | 5eeef3c | /schedules CRUD（operator+/delete admin）+ trigger_due（逐条独立 commit、失败 denied 不推进） |
| Task 2+6 Webhook+Web | 08f09b9 | services/webhooks.py（HMAC 签名、3 次退避、EXHAUSTED）+ GET /jobs/{key}/callbacks；Web 定时任务页/管道页/回调卡/菜单路由 |
| Task 4 管道 | 4bef6d9 | /pipelines CRUD+execute+runs；start_run/advance_runs（$prev 替换、失败即停、审计） |
| Task 5 接线 | 8583075+b16b9f1 | main.py 挂载三 router；POST /jobs 可选 callback 登记；scheduler 注册 deliver_due/trigger_due/advance_runs |
| Task 7 文档 | 87ee255 后续 | 指南自动化章节；README；验收报告 V2.2 节；集成 test_automation_lifecycle.py；服务说明-V2.2.md |

实施方式：Task 2/3/4/6 由四个并行子智能体完成（Task 2 因并发超限重发一次；
Task 3/4/6 主会话独立验收后提交）；主会话负责 Task 1/5/7。
质量门：python 非集成 256+ passed；web 153 passed + typecheck/lint/build；mypy strict 69 文件无问题。
服务说明：`docs/service-docs/服务说明-V2.2.md`。

---

# V2.3 平台体验（2026-09-14，spec/plan e2f6395）

| 任务 | 提交 | 内容 |
| --- | --- | --- |
| Task 1 仓库+秒传 | 753292a | /registry/plugins 列表、/registry/download/{build_key}（publisher+，StreamingResponse+审计 registry.download）、GET /files/by-sha256（operator+，open_available 同语义）；hubctl registry list/pull/sync |
| Task 2 SSE 日志流 | ddda6d6 | routers/logs_stream.py：游标增量+读尽自动推进、终态 event:end、300s 上限、断开回收、Job 删除优雅收尾 |
| 路由挂载 | bc75d56 | main.py 挂载 registry/logs_stream |
| Task 3 Web | af228f7 | 仓库页（列表+下载 saveBlob）、文件页秒传查重块、useJobLogStream（可注入 EventSource 工厂，终态降级轮询）、任务详情日志卡切换逻辑；顺手修复 automation.ts 预存 URL 断言 lint 失败 |
| Task 4 文档 | 本次 | 指南"仓库、秒传与实时日志"章；README；验收报告 V2.3 节；服务说明-V2.3.md |

实施方式：Task 1/2/3 由三个子智能体并行/接续完成，主会话验收提交并完成挂载与文档。
质量门：python 非集成 273 passed；web 166 passed + typecheck/lint/build；mypy strict 71 文件无问题。
分片/断点续传上传延后至 backlog（秒传已覆盖主要痛点）。
服务说明：`docs/service-docs/服务说明-V2.3.md`。

---

# V3.0 长期运行 Docker 服务（2026-09-15，spec/plan 见 docs/superpowers）

目标：在不改变既有一次性 Job / Runner 协议的前提下，让管理员在 Hub 内部署和运维常驻
服务容器；Hub API 仍然不接触 Docker Socket。

| 任务 | 提交 | 内容 |
| --- | --- | --- |
| Task 1 服务 API 与内部客户端 | d0ec88f + 06cb4a1 | 迁移 0006 `service_defs`；`routers/services.py`（列表/日志 viewer，创建/更新/启停/删除 admin，写操作审计、失败回写 STOPPED）；`services/service_manager.py` 回环 httpx 客户端（令牌鉴权、404/5xx→统一 Hub 错误、502 SERVICE_MANAGER_UNAVAILABLE） |
| Task 2 service-manager 与安全闭环 | 9c6bbfa | `deploy/service_hub/service_manager_service.py`（127.0.0.1:8001、Bearers 令牌、`hub-svc-<name>`、端口强制 127.0.0.1、替换同名容器、`unless-stopped`、非 root 65532）；supervisord 增 `service-manager` 进程（user=service-mgr）；Dockerfile 增 `service-mgr` 账号 |
| Task 3 Web 服务页 | 本次（前端 2 组提交） | `ServicesPage.tsx`（列表/部署弹窗/端口映射多行/环境变量与挂载文本解析+前端校验/生命周期按钮随实际状态/删除仅 admin+Popconfirm/日志 tail 弹窗/502 文案）；`api/services.ts`；`queryKeys.services`；路由与侧边栏「服务」；`ServicesPage.test.tsx` 9 条 |
| 修复：manager 无法访问 Docker Socket | 本次 | `docker-entrypoint.sh` 原先只把 `docker-runner` 加入 Socket 组，`service-mgr` 未加入，导致真机上 manager 调用 Docker 必然权限失败；现改为只授权两个 Socket 消费者（`docker-runner` + `service-mgr`），并以测试锁定该集合 |
| Task 4 V3 验收与运维文档 | 本次（文档提交） | `docs/service-docs/服务说明-V3.0.md`；`docs/service-docs/V3.0-部署验收清单.md`（终态清单 + 端到端步骤 + 9 类失败排查 + 门禁命令）；README 架构图/目录/入口与 V3.0 版本说明；`test_container_smoke.py` 增镜像内 manager、Socket 组归属、Web 无挂载断言；`test_web_console_lifecycle.py` 增 compose 边界 + 容器内 manager RUNNING + Socket GID 归属断言；`test_documented_commands.py` 增 V3 文档一致性断言 |

实施要点：

- `useMutation({ mutationFn: updateService })` 是真实缺陷：react-query 按 `(variables, context)`
  调用，而 `updateService` 是 `(name, request)` 双参数，直接透传会把整个请求体塞进 name。
  已改为显式包装（同时消除 typecheck 报错）。
- 前端全量 vitest 在本机并行时出现 7 个「Test timed out」假失败（单文件运行全绿，属资源争用）：
  在 `vite.config.ts` 设 `testTimeout: 20_000`，并让交互测试用 `userEvent.setup({ delay: null })`。
- `docker-entrypoint.sh` 的 Socket 组授权集合用测试锁定为 `{docker-runner, service-mgr}`，
  防止后续误把 `hub-api`/`conda-runner` 拉入。
- 顺手修复 `alembic/versions/0005_automation.py` 两处 E501（换行，无语义变化）。
- Linux AMD64 真机项（镜像构建、两个集成测试、发布包）仍需验收机执行并回填报告。

## 开发机质量门（V3.0）

- `python -m pytest -m "not integration"`：705 passed、3 skipped（7 deselected，246s）
- `python -m ruff check .`：All checks passed
- `python -m mypy packages`：Success，73 文件无问题
- `web`：vitest 全量 27 个测试文件通过；typecheck / lint / build 通过
- `HUB_HOST_DATA_DIR=/srv/service-hub-data docker compose config --quiet`：通过（本机无运行中
  的 Docker daemon，`compose config` 不需要 daemon）

## V3.0 提交序列

| 提交 | 说明 |
| --- | --- |
| d0ec88f | 服务定义模型与 service-manager 骨架 |
| 9c6bbfa | service-manager Docker 执行器 |
| 06cb4a1 | 长期服务 API、manager 客户端与审计 |
| 376eb87 | Web 服务页 + 9 条工作流测试 + vitest 超时配置 |
| fdd9b46 | 修复 socket 组授权（补充 service-mgr）与边界测试 |
| c18f5dc | 修复 0005 迁移两处 E501 |
| 本次 | V3.0 服务说明与部署验收清单、README、文档一致性测试 |

Linux 侧待验收项同样列入 `docs/service-docs/V3.0-部署验收清单.md` 第 1 节。

---

# V3.1 稳定性与正确性（2026-09-14，spec/plan 80f0981）

| 任务 | 提交 | 内容 |
| --- | --- | --- |
| Task 1 生命周期+reaper | da475c8（含 Task 2 合入） | RetentionSettings 增 job_retention_days/audit_retention_days；sweep_terminal_jobs/sweep_expired_sessions/sweep_old_audit_logs 三组清理；reaper 僵尸 Job 回收（PREPARING/RUNNING 超时→TIMED_OUT）；scheduler 注册；35 新测试 |
| Task 2 正确性修复 | 同上合入 | API Key 创建角色钳制（堵提权口子）；latest_version 点分整数排序（修 9.0>10.0 bug）；13 新测试 |
| Task 3 SSE 增量读+401 | d7e7324 | logs_stream 字节偏移增量读（无缓冲 FileIO、残行跨轮、终态冲刷）；Web 全局 401 拦截跳登录（handleUnauthorized 纯函数+测试） |
| Task 4 快修 | e6bec70 | metrics 30s 进程内缓存（_clear_metrics_cache 测试钩子）；compose 日志轮转 |
| 真实 NC 验收 | 服务器执行 | 样本 159MB 上传→docker Job SUCCESS→39464 要素/EPSG:4326/组件集完整 |

**实施方式**：Task 1/2/3 由三个子智能体并行完成（Task 2 因并发超限重发一次），
主会话验收提交并完成 Task 4。Task 1 提交时因并行写同一目录连带收入了 Task 2 的
进行中文件（代码已验证正确，提交信息已 amend 覆盖）。

**质量门**：python 非集成 **841 passed / 0 failed**；ruff/mypy strict 76 文件无问题；
web 179 passed + typecheck/lint/build；compose config 通过。
**真实 NC docker 运行时验收通过**：39464 要素 / EPSG:4326 / 组件集完整。
服务说明：`docs/service-docs/服务说明-V3.1.md`。

---

# V3.2 日常效率（2026-09-14，spec/plan 42d1644）

| 任务 | 提交 | 内容 |
| --- | --- | --- |
| Task 1 Job 重跑+筛选分页 | d85f73f | POST /jobs/{key}/rerun（复用 params_json/inputs_json + 配额/Build 校验/审计 job.rerun）；GET /jobs 增 status(多值)/plugin_id/limit/offset + total；迁移 0008 jobs.replayed_from；JobResponse 增 replayed_from；24 新测试 |
| Task 2 Webhook 重放 | c33f93b | POST /jobs/{key}/callbacks/{id}/replay（operator+，FAILED/EXHAUSTED→PENDING+审计 webhook.replay）；13 新测试（含真实再投递验证） |
| Task 3 Web 效率界面 | f4ee04d | 任务列表 Segmented+插件筛选+服务端分页；失败行/详情页重跑按钮；详情页进度条（Progress）；回调重放按钮；日志查看器（关键字过滤+ERROR/WARN 着色+全量下载）；192 tests |
| Task 4 收尾 | 本次 | 全量回归 871/0 + web 192/0；服务说明-V3.2.md；修改记录 |

**实施方式**：Task 1/2/3 由三个子智能体并行完成（Task 1 因并发超限重发一次），
主会话验收提交。Web 子智能体发现并修复 axios 数组参数序列化坑
（`status[]=RUNNING` → `status=RUNNING`，有专门测试锁定 wire 格式）。

**质量门**：python 非集成 **871 passed / 0 failed**；ruff/mypy strict 76 文件无问题；
web **192 passed** + typecheck/lint/build；compose config 通过。
服务说明：`docs/service-docs/服务说明-V3.2.md`。

---

# V3.3 治理完善（2026-09-14，spec/plan 327a90c）

| 任务 | 提交 | 内容 |
| --- | --- | --- |
| Task 1 备份预检+reconcile | 9298c3c | backup.py 磁盘剩余空间预检（409 DISK_SPACE_INSUFFICIENT）；services/reconcile.py 遍历 desired=RUNNING 的 ServiceDef → client.start 纠偏 + 审计；scheduler 注册 |
| Task 2 Webhook 投递明细 | d1e4f81 | WebhookDelivery 模型 + 迁移 0009；_deliver_one 逐次写投递历史（ok/status_code/error）；GET /jobs/{key}/callbacks/{id}/deliveries（viewer 倒序 100 条）；8 新测试 |
| Task 3 文件删除+CSV | 0062020 | DELETE /files/{key}（引用保护 409 FILE_IN_USE + unlink + 审计）；GET /audit-logs/export（admin CSV 流式+注入防护）；GET /jobs/export（operator CSV）；10 新测试 |
| Task 4 收尾 | 本次 | 全量回归 899/0；服务说明-V3.3.md；修改记录 |

**实施方式**：Task 1/2/3 由三个子智能体并行完成（Task 2 因并发超限重发一次），
主会话验收提交。
**质量门**：python 非集成 **899 passed / 0 failed**；ruff/mypy strict 77 文件无问题；
compose config 通过。web 192 passed（V3.2 基线，V3.3 无前端改动）。
服务说明：`docs/service-docs/服务说明-V3.3.md`。

---

# V4.0 Phase 1 功能插件化内核（2026-09-14，spec 6f5cff3 / plan aefae2d）

| 任务 | 提交 | 内容 |
| --- | --- | --- |
| Task 1 内核基础设施 | d480fac | kernel/protocol.py（FeaturePlugin/BaseFeaturePlugin/EventEnvelope/EventBinding/SchedulerTaskSpec/RouterMount/PluginContext）；kernel/registry.py（resolve_plugins 拓扑排序+依赖闭包校验）；kernel/events.py（EventBus 进程内同步+SQLite outbox）；HubEvent 模型 + 迁移 0010；9 新测试 |
| Task 2 前置迁移+耦合解除 | 518f7c4 | 解除 jobs.pipeline_run_id FK（逻辑引用）；_file_response 保留在 files.py（后续下沉）；恢复 pipeline_run_id 类型 |
| Task 3+4 插件拆分 | 386a02f | schedules + webhooks 包装为 FeaturePlugin 子类；main.py 从硬编码 include_router 改为按插件声明挂载 |
| Task 5 回归+文档 | 本次 | 全量 451/0；服务说明-V4.0-alpha.md；修改记录 |

**质量门**：python 非集成 451 passed / 0 failed（含 9 内核测试）；ruff/mypy strict 通过。
**待做**：Phase 1 剩余插件拆分（catalog/jobs/files/audit）+ 前端能力动态化。
