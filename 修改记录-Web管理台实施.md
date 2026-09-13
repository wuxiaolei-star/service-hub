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

---
