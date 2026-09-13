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
