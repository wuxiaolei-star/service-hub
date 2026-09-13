# Service Hub V2.0 认证、RBAC 与审计实施计划

> **For agentic workers:** 按任务逐个实施，每任务遵循 TDD（先失败测试再实现）并独立提交。
> 设计依据：`docs/superpowers/specs/2026-09-13-auth-rbac-audit-v2.0-design.md`
> 分支：`feature/v2-auth-rbac-audit`（堆叠于 `feature/service-hub-web-console-impl`）

**Goal:** 为 `/api/v1` 引入登录认证、可撤销会话、四角色 RBAC、API Key 机器账号与全量写操作审计，同时保持协议、既有 API 字段与 `/internal/v1` 边界完全不变，并提供 `HUB_AUTH_MODE=off` 过渡开关。

**Tech Stack:** FastAPI 依赖注入、SQLAlchemy/Alembic、argon2-cffi（唯一新增依赖）、SQLite、React/Vitest（Web 侧）。

## Global Constraints

- 既有 API 请求/响应字段零改动；新增端点统一使用现有 error envelope；
- `/internal/v1` 与 Runner Token 机制不动；
- 免认证端点仅 `GET /api/v1/system/health` 与 `POST /api/v1/auth/login`；
- 前端持久化不存任何凭证（token 只存 HttpOnly Cookie）；
- 每任务一个提交；文档/修改记录随任务同步更新。

---

### Task 1: 数据模型与迁移

**Files:** Modify `packages/hub-server/src/hub_server/models.py`、`alembic/versions/`；Create `tests/server/test_auth_models.py`

- Produces: `UserRecord`（username/role/password_hash/is_active/must_change_password）、`ApiKeyRecord`（name/key_prefix/key_hash/role/last_used_at/revoked_at）、`SessionRecord`（token_hash/expires_at/revoked_at/ip/user_agent）、`AuditLogRecord`（actor_type/actor_id/actor_name/action/resource_type/resource_id/detail/ip/result）；
- Alembic 迁移创建 4 表；role 列 CHECK 约束四值；默认测试断言唯一约束与级联行为。

### Task 2: 认证核心（口令、Token、Key、配置）

**Files:** Create `packages/hub-server/src/hub_server/services/auth.py`、Modify `settings.py`；Create `tests/server/test_auth_service.py`

- Produces: `hash_password/verify_password`（argon2）、`generate_token()`、`token_hash()`、`create_session/resolve_session/revoke_session`、`create_api_key/resolve_api_key/revoke_api_key`（双 actor 统一 `resolve_actor`）；
- Produces: `AuthSettings{mode: required|off, session_ttl_hours=24}` 进入 `HubSettings`（extra=forbid），`HUB_AUTH_MODE` 环境变量覆盖；
- 约束：明文凭证只出现一次；哈希查找 O(1)；过期/撤销即时生效。

### Task 3: 认证依赖与角色矩阵

**Files:** Create `packages/hub-server/src/hub_server/dependencies_auth.py`（或并入 dependencies.py）、Modify 各 router + `main.py`；Create `tests/server/test_auth_matrix.py`

- Produces: `require_auth` 依赖（双通道解析 Cookie/Bearer → actor），`require_role("viewer"|"operator"|"publisher"|"admin")`；
- 行为：`AUTH_REQUIRED`(401)/`FORBIDDEN`(403)/`INVALID_CREDENTIALS`(401)；角色层级 viewer<operator<publisher<admin；
- 矩阵按设计文档第 4 节逐端点应用到 routers；`HUB_AUTH_MODE=off` 时依赖直通并记 anonymous。

### Task 4: Auth 端点与初始引导

**Files:** Create `routers/auth.py`、Modify `main.py`、`entrypoint/启动流程`；Create `tests/server/test_auth_endpoints.py`

- Produces: `POST /auth/login`（口令校验、签发 Cookie+token、失败统一 INVALID_CREDENTIALS、审计）、`POST /auth/logout`、`GET /auth/me`、`POST /auth/change-password`；
- Produces: 启动时 users 为空且 mode=required → 生成 admin 写 `/data/bootstrap-admin.json`（0600）+ `must_change_password` 标志；change-password 清除标志；
- Cookie：HttpOnly、SameSite=Lax、Secure 跟随 X-Forwarded-Proto。

### Task 5: 用户与 API Key 管理端点

**Files:** Create `routers/users.py`、`routers/api_keys.py`；Create `tests/server/test_users_api.py`、`test_api_keys_api.py`

- Produces: 用户 CRUD（admin：列表/创建/停用/重置口令/改角色）；API Key（operator+ 自管，admin 全局：创建一次性返回 `hub_` 明文、列表含前缀、吊销）；
- 审计覆盖全部管理操作。

### Task 6: 审计打点与查询端点

**Files:** Create `services/audit.py` helper、Modify 写操作 routers；Create `tests/server/test_audit_api.py`

- Produces: `GET /api/v1/audit-logs`（admin，倒序分页，actor/action 过滤）；
- 打点动作清单按设计第 7 节（含 login 失败、下载动作）。

### Task 7: hubctl 认证支持

**Files:** Modify `tools/hubctl`；Create `tests/tools/test_hubctl_auth.py`

- Produces: `login`（交互口令、写 `~/.hubctl/credentials` 0600）、`logout`、`whoami`；
- `HUB_TOKEN` 环境变量优先；401 输出"凭证失效，请重新 login"退出码 1。

### Task 8: Web 管理台认证界面

**Files:** Create `web/src/pages/LoginPage.tsx`、`UsersPage.tsx`、`ApiKeysPage.tsx`、`AuditPage.tsx`、Modify api client（401 拦截）、`AppLayout`（用户菜单）、router；Create 对应测试

- Produces: 登录页、路由守卫（未登录仅可达登录页）、401 全局拦截、用户菜单（me/改密/登出/must_change_password 提示）、用户管理（admin）、API Key 管理（创建后一次性展示明文）、审计只读页；
- token 只存 HttpOnly Cookie，不落 localStorage。

### Task 9: 文档、发布包与验收

**Files:** Modify `README.md`、`docs/guides/*`、`deploy/release/*`（.env 支持可选 `HUB_AUTH_MODE`）、`docs/reports/*`；Create `tests/integration/test_auth_lifecycle.py`

- Produces: 指南新增认证章节（引导文件、login、API Key、关闭模式、升级路径）；发布包脚本透传 `HUB_AUTH_MODE`；验收报告新增 V2.0 章节；
- 集成测试：required 模式全新部署引导流程、login→操作→audit 落库、off 模式等价性、矩阵抽查、Key 撤销即时生效。

---

## 执行顺序与检查点

- Task 1–4 完成后：认证核心可端到端工作（引导→登录→带角色访问→撤销），是后续任务的地基；
- Task 5–7 完成后：管理端点、审计与命令行工具齐备；
- Task 8–9 完成后：Web 界面、文档与验收闭环，进入 Linux AMD64 验收。
