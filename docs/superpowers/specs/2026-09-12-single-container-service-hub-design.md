# Python Service Hub 单容器双运行时 V1 设计

- 日期：2026-09-12
- 状态：待用户最终审阅
- 目标平台：Linux AMD64
- 部署形态：一个镜像、一个长期运行容器、一个持久化数据目录

## 1. 背景与目标

用户经常临时编写包含不同算法和数据处理逻辑的 Python 脚本。这些脚本依赖差异大，
有些只依赖普通 Python 包，有些依赖 GDAL、PROJ、HDF5 等系统组件。需要一个长期
运行的工具服务，将脚本作为插件集中注册、按任务执行、保存日志并返回输出。

现有系统已具备 Hub API、conda-pack Runner、Docker Runner、PluginBuild、File 和 Job，
但部署包含三个长期容器。本次调整在保留双运行时和现有插件协议的前提下，将它简化为
一个可直接导入和启动的 Docker 服务。

已确认目标：

1. Service Hub 以一个 Docker 镜像交付，服务器上只有一个长期 Hub 容器。
2. 容器内部同时支持 conda-pack 和 Docker 两种插件运行时。
3. V1 插件均为一次性 Job，完成、失败、取消或超时后退出。
4. 用户在目标平台提前构建插件依赖；Hub 不执行 pip、conda 在线安装或本地编译。
5. 新增业务插件不需要重新构建 Hub 镜像。
6. NC→SHP 必须通过两种运行时的完整验收。
7. 提供简单部署脚本、调用工具和插件模板。

本轮不实现长期运行的 Docker 服务插件、服务发现、插件端口代理、多节点、Kubernetes、
多租户、Hub 应用层认证、不可信代码强沙箱或 ARM64 实际验收。长期服务后续单独设计。

## 2. 核心概念

Docker 和 conda-pack 是 Hub 内置运行驱动，不是业务插件或其他插件依赖的基础插件。

~~~text
Service Hub
├── REST API、插件、文件和 Job 管理
├── conda-pack 运行驱动
└── Docker 运行驱动

业务插件
├── nc_to_shp
├── excel_cleaner
├── raster_statistics
└── 其他算法脚本
~~~

一个插件版本可以选择一种或同时提供两种 PluginBuild：

~~~text
nc_to_shp / 1.0.0
├── linux-amd64 / conda-pack
└── linux-amd64 / docker
~~~

## 3. 目标架构

~~~mermaid
flowchart TB
    Client[外部系统或 hubctl] -->|REST API| API
    subgraph Container[service-hub 单一长期运行容器]
        PID1[tini] --> Supervisor[supervisord]
        Supervisor --> API[Hub API]
        Supervisor --> Conda[conda-pack Runner]
        Supervisor --> DockerRunner[Docker Runner]
        Conda -->|localhost internal API| API
        DockerRunner -->|localhost internal API| API
    end
    Container --> Data[(宿主机持久化目录)]
    DockerRunner -->|Docker Socket| Engine[宿主机 Docker Engine]
    Engine --> Task[一次性 Docker 插件任务容器]
    Task --> Data
~~~

外部只有一个 Hub 容器。Docker 插件按 Job 临时产生的任务容器不是长期部署服务，Job
结束后删除。conda-pack 插件作为 Hub 容器内的一次性非 root 子进程运行。

## 4. 容器内部进程

| 进程 | 职责 | 失败处理 |
|---|---|---|
| hub-api | REST API、数据库、文件和任务状态 | 自动重启并导致健康检查失败 |
| conda-runner | 安装 conda-pack Build、执行一次性进程 | 恢复当前 Job 后自动重启 |
| docker-runner | 导入镜像、执行一次性任务容器 | 清理任务容器并自动重启 |

tini 作为 PID 1，负责信号转发和子进程回收；supervisord 管理三个业务进程的启动、日志
和异常重启。继续复用现有三个 Python 入口，不将 Runner 改成 Hub 内部线程。

启动顺序：

1. 校验 /data 可写；
2. 校验 /var/run/docker.sock 存在且可连接；
3. 创建数据库、文件、插件、环境、Job、日志和密钥目录；
4. 创建或读取内部 Runner Token；
5. 按 Docker Socket GID 配置 Docker Runner 权限；
6. 执行数据库迁移；
7. 启动 supervisord 和三个业务进程；
8. Runner 访问本地内部接口并开始轮询。

Runner 内部地址统一为 http://127.0.0.1:8000/internal/v1。Runner 必须在 Hub 尚未就绪时
退避重试，不能因一次 connection refused 永久失败。

停止时，tini 将 SIGTERM 转发给 supervisord；Runner 停止领取任务，终止当前插件，
Docker Runner 清理归属任务容器，Hub 完成数据库写入后退出。异常中断的 Job 在下次
启动时通过现有 reconcile 机制进入明确终态，不得永久停留在 RUNNING。

## 5. 权限与信任边界

镜像创建三个非 root 业务用户和一个共享数据组：

~~~text
hub-api 用户：访问 Hub 数据和 SQLite，无 Docker Socket 权限
conda-runner 用户：访问环境和 Job 工作区，无 Docker Socket 权限
docker-runner 用户：访问 Job 工作区，唯一拥有 Docker Socket 权限
~~~

supervisord 可以由 root 启动，但每个业务进程必须降权。入口读取 Docker Socket 的实际
GID，创建或复用对应组，只加入 docker-runner；禁止 chmod 666 Docker Socket。

单容器内的 Unix 用户隔离不等价于三个独立容器的内核隔离。V1 只接受可信内部管理员
构建和注册的插件。conda-pack 插件不是安全沙箱，但不得获得 Docker Socket 权限。
Docker 插件默认无网络、非 root、只挂载当前 Job 输入输出路径，并受取消和超时控制。

## 6. 配置与持久化

宿主机默认目录为 /srv/service-hub-data，容器目录为 /data：

~~~text
/data
├── db/hub.db
├── files/
├── plugins/
├── environments/
├── jobs/
├── logs/
└── secrets/runner-token
~~~

重建容器不得删除或覆盖这些数据。Docker 任务由宿主 Engine 创建，因此必须显式传入：

~~~text
HUB_HOST_DATA_DIR=/srv/service-hub-data
~~~

该变量同时作为目录挂载源和 Docker Runner 的任务 bind source。入口拒绝空值、相对
路径、根目录及其他危险路径。

镜像内置单机默认配置：

~~~yaml
deployment:
  mode: offline
storage:
  root: /data
database:
  url: sqlite:////data/db/hub.db
uploads:
  max_size_bytes: 10737418240
runner:
  poll_interval_seconds: 2
~~~

首次部署无需创建 hub.yaml，高级用户可只读挂载自定义配置。

如果未提供 HUB_RUNNER_TOKEN，入口首次启动时生成高熵 Token 并保存在
/data/secrets/runner-token，重启后复用。Token 不得写入普通日志。

## 7. 单镜像和部署

统一 Dockerfile 基于 Python 3.12 slim，包含 Hub 四个内部包、Docker Python SDK、
zstandard、tini、supervisord、迁移文件、三个进程入口、默认配置和健康检查。

运行镜像不包含 NC→SHP、用户脚本、完整 Conda 构建工具、GDAL 等业务依赖或 Docker
Daemon。conda-pack 环境自带 Python 和依赖；Docker 插件依赖封装在插件镜像内。

compose.yaml 只保留一个服务，逻辑等价于：

~~~yaml
services:
  service-hub:
    image: python-service-hub:1.0.0-linux-amd64
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      HUB_HOST_DATA_DIR: $HUB_HOST_DATA_DIR
    volumes:
      - $HUB_HOST_DATA_DIR:/data
      - /var/run/docker.sock:/var/run/docker.sock
~~~

直接运行：

~~~bash
docker run -d \
  --name service-hub \
  --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -e HUB_HOST_DATA_DIR=/srv/service-hub-data \
  -v /srv/service-hub-data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  python-service-hub:1.0.0-linux-amd64
~~~

离线发布包：

~~~text
service-hub-linux-amd64/
├── service-hub-image.tar
├── SHA256SUMS
├── compose.yaml
├── install.sh
├── start.sh
├── stop.sh
├── status.sh
├── hubctl
└── README.md
~~~

用户主要执行 sudo ./install.sh、sudo ./start.sh、sudo ./status.sh。安装脚本校验摘要、
创建受限数据目录并 docker load；所有脚本可重复执行且不得删除已有数据。

## 8. 插件模型

V1 插件统一为 run-to-completion：

~~~text
输入文件和参数
→ 创建 Job
→ 启动一次插件进程或任务容器
→ 写 result.json 和输出文件
→ 退出
~~~

继续使用现有不可变 .pypkg：

~~~text
plugin.yaml
build.json
src/
runtime/env.tar.zst 或 image.tar.zst
~~~

安装流程继续执行流式上传、SHA256、安全解包、Pydantic 校验、平台和运行时校验、Runner
安装以及数据库原子注册。Build 到 READY 后由用户启用，只有 ENABLED Build 可执行 Job。
同一 Build 不允许覆盖，修改源码或依赖必须生成新 Build 或提升插件版本。

本次不改变 plugin.yaml、build.json、Job Runtime Protocol、Runner Event Protocol、
公共 REST API、数据库模型和现有数据布局。

## 9. 插件作者体验

conda-pack 模板：

~~~text
my-script/
├── plugin.yaml
├── environment.yml
└── src/main.py
~~~

Docker 模板：

~~~text
my-docker-plugin/
├── plugin.yaml
├── Dockerfile
└── src/main.py
~~~

统一构建命令：

~~~bash
hub-plugin build ./my-script --runtime conda-pack --arch amd64
hub-plugin build ./my-docker-plugin --runtime docker --arch amd64
~~~

hub-plugin 是发布者侧工具，不在 Hub 服务中在线安装依赖。它校验插件目录，调用目标平台
Conda 或 Docker，生成运行归档、build.json、摘要和规范命名的 .pypkg。

插件通过 SDK 获得输入、参数、输出、日志、进度和取消状态，读取 job.json 并写
result.json。简单脚本模板只要求作者实现签名为
run(context: PluginContext) -> PluginResult 的入口函数。

## 10. 管理工具

发布包提供轻量 hubctl，包装现有 REST API：

~~~text
hubctl health
hubctl plugin list
hubctl plugin install PACKAGE
hubctl plugin enable BUILD_ID
hubctl file upload FILE
hubctl job run PLUGIN --version VERSION --runtime RUNTIME --input NAME=FILE_ID --params FILE
hubctl job status JOB_ID
hubctl job logs JOB_ID
hubctl job cancel JOB_ID
hubctl job download JOB_ID --output DIR
~~~

hubctl 默认访问 http://127.0.0.1:8000，可通过 HUB_URL 覆盖。它不新增服务端协议，
外部 Java、Python 等系统仍可直接调用 REST API。

## 11. 健康检查与错误处理

Docker HEALTHCHECK 至少确认 Hub HTTP 端点、三个 supervisord 进程、/data 可写和 Docker
Socket 可连接。所有服务日志输出到 stdout/stderr 并带进程前缀，插件日志继续通过 Job
Log API 保存。

| 场景 | 结果 |
|---|---|
| Hub API 启动失败 | unhealthy，进程自动重启 |
| Runner 暂时无法连接 Hub | 退避重试 |
| 插件非零退出 | Job FAILED |
| 用户取消 | 终止进程或容器，Job CANCELLED |
| 超过 timeout | 强制终止，Job TIMED_OUT |
| 容器异常重启 | reconcile 中断 Job，清理归属容器 |
| Docker Socket 缺失 | 入口输出明确错误并退出，由 Docker 重启策略处理 |
| /data 不可写 | 快速失败 |
| Build 平台不匹配 | 拒绝安装 |

插件失败不得导致整个 Service Hub 容器退出。

## 12. 兼容与迁移

从三容器版本迁移：

1. 停止旧 Compose；
2. 备份数据目录、配置和 Token；
3. 导入单容器镜像；
4. 使用同一宿主数据目录启动；
5. 执行数据库迁移；
6. 验证已有 PluginBuild、File 和 Job；
7. 执行两种运行时冒烟任务；
8. 验收后移除旧容器，保留备份。

迁移工具不得自动删除旧镜像、旧容器或数据。

## 13. 测试与 NC→SHP 验收

单元测试覆盖路径校验、Token、Socket GID、连接重试、健康检查、hubctl 和 hub-plugin。
容器测试覆盖单服务、三个内部进程、权限、SIGTERM、进程恢复和数据保持。集成测试覆盖
Build 安装启用、两种 Job、文件、日志、取消、超时和旧数据迁移。

NC→SHP 分别构建：

~~~text
nc_to_shp-1.0.0-linux-amd64-conda.pypkg
nc_to_shp-1.0.0-linux-amd64-docker.pypkg
~~~

使用真实 NC 上传并以相同参数运行两个 Job。两个 Job 必须为 SUCCESS；下载的 ZIP 按
组件集合、PointZ、CRS、字段、SMID、全量坐标和属性比较；每个 Shapefile 必须为
39,464 个要素。重启 Hub 后插件、文件和 Job 仍可查询。

## 14. 交付验收标准

- [ ] 只构建一个 Service Hub 运行镜像；
- [ ] compose.yaml 只有一个长期服务；
- [ ] 一条命令可启动，一个目录保存全部数据；
- [ ] 用户无需手工生成 Runner Token；
- [ ] 三个内部进程均被监控；
- [ ] Hub API 和 conda-runner 无 Docker Socket 权限；
- [ ] Docker Job 临时创建并清理任务容器；
- [ ] conda-pack Job 启动一次性进程并退出；
- [ ] 现有公共 API、.pypkg 和数据保持兼容；
- [ ] NC→SHP 两种运行时均为 SUCCESS；
- [ ] 两份输出通过 39,464 要素语义比较；
- [ ] 容器重启后数据保持；
- [ ] Linux AMD64 离线包可导入和启动；
- [ ] 部署脚本、hubctl、插件模板和说明完整。

## 15. 后续演进

长期 Docker 服务插件后续单独增加 execution.mode=service、ServiceInstance、启动停止、
健康检查、端口路由、升级和回滚。在该设计完成前，V1 明确拒绝常驻插件。

本次改造的本质是外部从三个长期容器整合为一个长期容器；内部职责、双运行时、插件
预构建原则和协议继续保留。
