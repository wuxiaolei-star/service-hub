# V3.0 长期运行 Docker 服务设计

## 目标

在不改变现有一次性 Job、Conda Runner、Docker Runner 边界的前提下，为 Service Hub
增加由管理员注册、启动、停止、重启、删除和查看日志的长期 Docker 服务能力。

## 边界

- Hub API 不直接访问 Docker Socket；只有容器内的 `service-manager` 子进程访问它。
- 长期服务与一次性插件 Job 是两条独立生命周期：ServiceDefinition 保存期望状态，Job
  仍使用现有 Runner 协议。
- 服务只能由管理员写入或执行生命周期操作；已登录用户可读取列表、状态和日志。
- 服务端口固定绑定宿主机 `127.0.0.1`，由外层 Nginx 或其它受控代理对外暴露。
- 服务容器使用 `hub-svc-<name>` 命名、`unless-stopped` 重启策略及非 root 默认 UID。
- 挂载、镜像和命令属于管理员部署输入；V3 不实现镜像仓库策略、多主机编排或服务发现。

## 架构

```text
Web 管理台 --/api/v1/services--> Hub API --Bearer 内部令牌--> service-manager --Docker Socket--> hub-svc-<name>
                                      |                                      |
                                      +-- SQLite ServiceDefinition -----------+
```

Hub API 先验证用户角色并持久化期望配置，再调用本机回环 `service-manager`。管理器以
共享内部令牌验证请求，执行 Docker 操作并返回容器状态。Hub API 不将 Docker Socket 暴露给
Web、插件或普通业务请求。

## 数据与 API

`ServiceDefinition` 保存名称、镜像、端口、环境变量、挂载、命令、容器名和期望状态。
公开返回值不回显环境变量或挂载细节以外的敏感信息。API 包含：

- `GET /api/v1/services`、`GET /api/v1/services/{name}/logs`；
- `POST /api/v1/services`、`PUT /api/v1/services/{name}`；
- `POST /api/v1/services/{name}/start|stop|restart`、`DELETE /api/v1/services/{name}`。

写操作审计；服务管理器不可用时返回统一的 502 Hub 错误。部署或更新为替换同名容器的
幂等操作，失败时不得把数据库标记为 RUNNING。

## 验收

1. 无 Docker 环境的单元测试可验证鉴权、请求转发、状态映射与审计。
2. service-manager 单元测试可验证内部令牌、Docker 参数、日志、找不到容器及替换流程。
3. Web 测试覆盖列表、管理员操作、表单校验和日志弹窗；typecheck/lint/build 通过。
4. Linux AMD64 上，`docker compose up -d` 后可部署一个 HTTP 示例容器，只能从宿主机回环端口访问，停止/重启/删除行为正确。
