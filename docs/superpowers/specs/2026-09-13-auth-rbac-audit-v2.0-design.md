# Service Hub V2.0 认证、RBAC 与审计设计

- 日期：2026-09-13
- 状态：草案（待用户确认）
- 依据：`docs/design/路线图-V2.0至V3.0.md` V2.0 章节
- 上游基线：单容器双运行时 + Web 管理台（`feature/service-hub-web-console`）

## 1. 背景与目标

V1 的信任模型是"能到达 8080/8000 的都是可信内网用户"：Hub 无应用层认证，所有
`/api/v1` 操作无差别可用，管理台没有用户概念。管理台投入使用后，"谁在操作"必须
成为系统的一等概念，且它是 V2.1 配额、V2.2 自动化的前提。

已确认目标：

1. `/api/v1` 提供登录认证、可撤销会话与四种角色（RBAC）；
2. 业务系统以 API Key 机器账号接入，与人类账号统一权限模型；
3. 全部写操作落审计日志，管理台可查；
4. hubctl 与 Web 共用同一套认证体系；
5. 提供 `HUB_AUTH_MODE=off` 显式关闭开关，存量部署可先升级后配账号；
6. 协议与数据兼容：现有 plugin.yaml、Job/Runtime 协议、既有 API 响应字段全部不变。

本轮不实现：LDAP/OIDC 实际接入（只预留可替换认证层）、多租户、字段级权限、配额
（V2.1）、`/internal/v1` 变更（继续走 Runner Token，不在本设计范围）。

## 2. 已确认决策

| 决策 | 结论 |
| --- | --- |
| 初始 admin 引导 | 数据目录受限文件（对齐 `HUB_RUNNER_TOKEN` 模式） |
| 过渡开关 | 保留 `HUB_AUTH_MODE=off`，默认 `required` |
| 认证后端 | 本地口令优先，认证方式做成可替换层，预留 LDAP/OIDC |
| hubctl 与 M2M 凭证 | 统一走 `api_keys` 体系，不维护两套校验逻辑 |

## 3. 信任模型与边界

~~~text
浏览器 ──── Cookie hub_session (HttpOnly) ──┐
hubctl ──── Authorization: Bearer <token> ──┤
业务系统 ── Authorization: Bearer hub_<key> ┴──► Nginx/TLS ──► Hub /api/v1   [用户认证]
conda/docker Runner ── Bearer HUB_RUNNER_TOKEN ──► Hub /internal/v1          [服务间, 不变]
~~~

- 免认证端点仅保留 `GET /api/v1/system/health`（无敏感信息、供存活探测）与
  `POST /api/v1/auth/login`；其余 `/api/v1` 一律要求认证；
- `/internal/v1` 与 Runner Token 机制完全不动；
- `HUB_AUTH_MODE=off` 时全部 `/api/v1` 行为与 V1 相同（actor 记为 `anonymous`）。

## 4. 角色与端点权限矩阵

四个角色按现有 router 逐端点标注（`≥` 表示该角色及以上均可）：

| 端点组 | viewer | operator | publisher | admin |
| --- | --- | --- | --- | --- |
| `GET /system/info` | ≥ viewer | | | |
| `GET /files`、`GET /files/{id}` | ≥ viewer | | | |
| `POST /files`、`GET /files/{id}/download` | | ≥ operator | | |
| `GET /plugins`、`GET /plugins/{id}`、`GET /plugin-builds*` | ≥ viewer | | | |
| `POST /plugins/install`、`POST /plugin-builds/{id}/enable\|disable` | | | ≥ publisher | |
| `GET /jobs`、`/jobs/{id}`、`/jobs/{id}/logs`、`/jobs/{id}/outputs` | ≥ viewer | | | |
| `POST /jobs`、`POST /jobs/{id}/cancel` | | ≥ operator | | |
| `GET /api/v1/auth/*` | 登录用户 | | | |
| 用户/API Key 管理、`GET /api/v1/audit-logs` | | | | admin |

说明：数据类下载（文件、Job 输出）需要 operator——viewer 用于审计/监控场景，不允许
取走数据；日志端点保留给 viewer 以便观察执行进度。

## 5. 数据模型（Alembic 迁移新增 4 表）

~~~text
users        id, username UNIQUE, password_hash(argon2), role, is_active,
             must_change_password, created_at, updated_at
api_keys     id, name, key_prefix(明文前8字符, 便于辨认), key_hash, role,
             created_at, last_used_at, revoked_at
sessions     id, token_hash, user_id, created_at, expires_at, revoked_at,
             ip, user_agent
audit_logs   id, at, actor_type(user|api_key|anonymous), actor_id, actor_name,
             action, resource_type, resource_id, detail(JSON), ip, result
~~~

- Token/Key 生成：`secrets.token_urlsafe(32)`；存储一律存 SHA256 哈希，按哈希查找
  （O(1) 校验，库泄露不还原凭证；Key 为 256 位随机值，SHA256 存储足以抗暴力）；
- API Key 明文只在创建响应中出现一次，格式 `hub_<43字符>`，`key_prefix` 用于列表辨认；
- 会话 TTL 固定 24 小时，登出/停用用户即时撤销。

## 6. 认证机制

- 登录：`POST /api/v1/auth/login {username, password}` → 校验 argon2 哈希 → 签发
  session → 响应 Set-Cookie `hub_session`（HttpOnly、SameSite=Lax、Secure 跟随
  `X-Forwarded-Proto`）+ JSON 返回 token（供 hubctl 等非浏览器场景）；
- 登出：`POST /api/v1/auth/logout` 撤销当前 session 并清 Cookie；
- 当前用户：`GET /api/v1/auth/me`；修改口令：`POST /api/v1/auth/change-password`；
- 双通道认证依赖：Cookie（浏览器）或 `Authorization: Bearer`（token 或 API Key），
  统一解析为 actor（user 或 api_key），FastAPI 依赖注入实现
  `require_role("viewer"|"operator"|"publisher"|"admin")`；
- 配置：`AuthSettings{mode: required|off, session_ttl_hours}` 进入 hub.yaml 严格配置
  模型（extra=forbid），环境变量可覆盖；
- 错误码沿用现有 error envelope：`AUTH_REQUIRED`（401）、`FORBIDDEN`（403）、
  `INVALID_CREDENTIALS`（401，登录失败统一文案，不区分用户不存在/口令错误）。

## 7. 审计日志

- 打点位置：router 层显式 helper（`audit(request, action, resource_type, resource_id, detail)`），
  actor 与 IP 由认证依赖注入到 request state；
- 记录动作清单：login 成功/失败、logout、change_password、file upload、plugin install、
  build enable/disable、job create/cancel、用户与 API Key 全部管理操作、下载
  （file download、job output download）；
- 查询：`GET /api/v1/audit-logs`（admin，按时间倒序分页，支持 actor/action 过滤）；
- 保留策略：V2.0 不自动清理（与 V2.1 文件生命周期统一考虑）；管理台加只读审计页。

## 8. 初始引导

- 启动时（Alembic 迁移后）若 `users` 为空且 `auth.mode=required`：生成随机 24 字符
  口令与 `admin` 用户，将用户名+一次性口令写入
  `${HUB_DATA_ROOT}/bootstrap-admin.json`（0600，hub-api 属主），日志只打印文件路径
  不打印口令；
- 该账号 `must_change_password=true`：不阻止使用，但登录响应携带标志，Web 首页提示
  修改；修改口令后标志清除；
- `HUB_AUTH_MODE=off` 模式不生成引导账号，首次切换为 required 时再生成。

## 9. Web 管理台

- 登录页；axios 拦截器 401 → 跳登录；路由守卫（未登录只可达登录页）；
- 顶部用户菜单：当前用户名/角色、修改口令（含 must_change_password 提示）、登出；
- 新增页面：用户管理（admin：列表/创建/停用/重置口令/改角色）、API Key 管理
  （operator+ 管理自己的 Key，admin 管全局；创建后一次性展示明文）、审计（admin，只读）；
- 约束延续：token 只存 HttpOnly Cookie，前端持久化仍不存任何凭证。

## 10. hubctl

- `login` 子命令：用户名+口令（交互输入）→ 获取 token → 写
  `~/.hubctl/credentials`（0600）；
- `HUB_TOKEN` 环境变量优先于 credentials 文件（值可以是 session token 或 API Key）；
- 收到 401 时输出"凭证失效，请重新 login"并退出码 1；`whoami` 子命令显示当前身份。

## 11. 兼容与迁移

- Alembic 迁移新增 4 表，不改既有表；
- 既有 API 请求/响应字段全部不变，只新增认证头/Cookie 与 auth、users、api-keys、
  audit-logs 四组新端点；
- 管理台、发布包（build-release.sh）、README 与指南同步更新；
- 升级路径：先以 `HUB_AUTH_MODE=off` 升级（行为与 V1 等价）→ 登录引导 admin →
  建业务账号与 API Key → 切回 required 重启。

## 12. 测试与验收策略

- 单元：token 签发/过期/撤销、argon2 哈希、角色矩阵参数化测试（逐端点 × 逐角色 ×
  开关两模式）、审计打点断言；
- API 集成：双模式部署、双通道认证（Cookie/Bearer）、API Key 撤销即时生效；
- Web：登录/守卫/用户管理/审计页组件测试；
- Linux AMD64 验收：默认 required 模式全新部署 → bootstrap 文件流程 → hubctl login →
  Web 登录与矩阵抽查 → `HUB_AUTH_MODE=off` 存量等价性验证。

## 13. 依赖变更

- 运行时新增：`argon2-cffi`（唯一新增，烘焙进离线镜像）；
- 其余全部使用现有技术栈（FastAPI 依赖注入、SQLAlchemy、Alembic）。
