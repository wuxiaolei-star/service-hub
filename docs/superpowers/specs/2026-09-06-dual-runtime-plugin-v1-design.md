# Python Service Hub V1：Conda-Pack 与 Docker 双运行时设计基线

## 1. 权威性、目标与范围

本文档是 Service Hub V1 的插件运行时权威设计。它取代下列文档中关于“插件环境
格式、执行器、Build 安装和运行”的单运行时描述，但保留其余仍兼容的协议、文件、
API 与安全原则：

- `docs/design/python-service-hub-v1-design.md` 的 Conda/ProcessExecutor 描述；
- `docs/superpowers/specs/2026-09-06-executable-service-hub-mvp-design.md` 的
  Conda-only MVP 描述；
- `docs/superpowers/specs/2026-09-06-plugin-runtime-v1-design.md` 的 Docker-only
  运行时描述。

V1 实现一个 Hub 服务，能够注册插件 Build、启动插件 Job、查询日志和下载输出。
插件发布者负责预构建目标平台的运行环境；Hub 只校验、安装、选择并启动环境，绝不
在服务器执行依赖解析、`pip install`、`conda install` 或 GDAL/PROJ 编译。

首个插件 `nc_to_shp` 必须由同一份源码发布两种 Linux AMD64 Build，并以同一真实 NC
输入、相同参数完成端到端和语义等价测试：

- `conda-pack`：预构建 Conda 环境 `env.tar.zst`；
- `docker`：预构建 Docker 镜像离线归档 `image.tar.zst`。

V1 只支持单机 Docker Compose、一个 Conda Worker、一个 Docker Worker、插件并发 1
和调用方轮询。非目标包括外部消息队列、WebSocket/SSE、多 Worker、插件常驻 HTTP
服务、远程容器平台、多租户和外部应用层认证。

## 2. 不变的设计原则

1. Hub 管生命周期，插件管业务，运行时环境管依赖，Runner 管启动。
2. `plugin.yaml` 保持平台无关；CPU/OS/运行时类型仅存在于 `build.json` 和
   `PluginBuild` 中。
3. PluginVersion、PluginBuild、输入快照、参数快照、环境 fingerprint 和输出文件哈希
   均不可变，以支持重现。
4. API 前缀固定为 `/api/v1`；失败固定为
   `{"success": false, "error": {"code", "message", "details"}}`。
5. Docker 和 Conda Build 是平等的显式选择，不自动回退到另一运行时。
6. Hub API 当前不做应用层认证；Nginx TLS、内网来源限制和防火墙是部署前提。

## 3. 架构与权限边界

```text
外部业务系统 → Nginx → hub-server
                          │
                          ├── SQLite /data、文件、Plugin/Build、Job API
                          │
                          ├──── runtime_type=conda-pack ───► hub-conda-runner
                          │                                  无 Docker Socket
                          │                                  解压并启动 Conda 子进程
                          │
                          └──── runtime_type=docker ───────► hub-docker-runner
                                                             挂载 Docker Socket
                                                             仅 docker load/run/stop
                                                                    │
                                                                    ▼
                                                          临时 nc_to_shp 容器
```

两个 Runner 使用同一套 Runner 协议代码，并共享只受控的 `/data` 持久化卷；但它们是
两个 Compose 服务、两个不同权限边界：

| 服务 | 对外端口 | Docker Socket | 可执行插件业务代码 |
| --- | --- | --- | --- |
| `hub-server` | 仅 `127.0.0.1:8000` | 否 | 否 |
| `hub-conda-runner` | 无 | 否 | 是，仅 Conda Build |
| `hub-docker-runner` | 无 | 是 | 否，只启动 Docker 容器 |
| Docker 插件容器 | 无 | 否 | 是，仅自身 Job |

Runner 经未发布端口的内部 Hub API 领取与自身 `runtime_type` 匹配的安装操作和 Job。
Compose 为内部 Runner API 配置随机高熵共享令牌；它不替代未来外部认证，只防止任意
同网络容器伪装 Worker。Hub 采用原子领取，确保单个 Job 或安装操作仅被一个 Runner
处理。

Docker 插件容器必须非 root、默认 `--network none`、没有 Docker Socket、没有
privileged 权限或额外 capabilities；只挂载输入只读、Job 描述只读和输出可写目录。

## 4. 双 Build 包格式与平台策略

同一 PluginVersion 在同一 CPU 架构可有两个 Build。它们分别传输，不合并成一个巨大
归档，避免无用环境占用离线介质并保持各自的内容哈希可复现。

```text
nc_to_shp 源码
  ├─ nc_to_shp-1.0.0-linux-amd64-conda.pypkg
  └─ nc_to_shp-1.0.0-linux-amd64-docker.pypkg
```

两个包的共同内容：

```text
plugin.yaml
build.json
plugin/                 # 不可变、已哈希的业务源码
checksums.json
```

Conda 包额外包含：

```text
runtime/env.tar.zst     # Linux 目标架构预构建的 conda-pack 环境
```

Docker 包额外包含：

```text
image.tar.zst           # Linux 目标架构预构建 Docker image archive
```

`build.json` 使用严格判别式 `runtime` 字段：

```json
{
  "plugin_id": "nc_to_shp",
  "plugin_version": "1.0.0",
  "target": {"os": "linux", "arch": "amd64"},
  "runtime": {
    "type": "conda-pack",
    "archive": "runtime/env.tar.zst",
    "fingerprint": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }
}
```

```json
{
  "plugin_id": "nc_to_shp",
  "plugin_version": "1.0.0",
  "target": {"os": "linux", "arch": "amd64"},
  "runtime": {
    "type": "docker",
    "archive": "image.tar.zst",
    "image": "nc_to_shp:1.0.0-linux-amd64",
    "digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }
}
```

`checksums.json` 列举归档中每一个允许文件的 SHA256；清单本身也受包 SHA256 保护。
真实构建必须写入其实际的完整 64 位十六进制摘要；以上数值仅为格式示例。

两种运行环境均须在发布阶段预装与 `plugin.yaml` 匹配版本的
`python-hub-sdk`、`python-hub-runner` 和业务依赖。Conda Runner 使用环境中的
`python -m hub_runner` 加载包内 `plugin/` 源码；Docker 镜像以同一 Runner 作为
entrypoint，并在镜像标签中记录插件源码 SHA256。Hub 只验证这些版本和哈希，不安装它们。

Linux AMD64 是当前验收平台。未来 Linux ARM64 必须使用同一 Hub/插件源码重新构建
两种 ARM64 Build：不得将 AMD64 Conda 环境或 Docker 镜像导入 ARM64 主机。

## 5. 安装、启用与不可变模型

数据库关系：

```text
Plugin 1 ── * PluginVersion 1 ── * PluginBuild 1 ── 1 Environment
                                           │
                                           *
                                           │
                                          Job * ── * FileRecord
```

`PluginBuild` 的唯一键是：

```text
(plugin_version_id, target_os, target_arch, runtime_type)
```

同一键不可覆盖；运行环境、插件源码或镜像变化必须创建新 PluginVersion 或新的、尚未
存在的目标 Build。Job 永远记录精确 `plugin_build_id` 和 runtime fingerprint。

安装流程：

```text
POST /plugins/install
  → hub-server 安全保存、解包、严格校验 manifest/checksums/平台
  → PluginBuild=INSTALLING
  → 对应 Runner 原子领取安装操作
     ├─ conda-pack：受限解压 env.tar.zst → conda-unpack → 导入健康检查
     └─ docker：受限解压 image.tar.zst → docker load → inspect digest → 导入健康检查
  → Environment=READY, PluginBuild=READY；失败则 FAILED，保留安全错误摘要
  → 以 build_id 显式 enable → PluginBuild=ENABLED
```

为消除双 Build 下的歧义，启用 API 使用 Build ID：

```http
POST /api/v1/plugin-builds/{build_id}/enable
POST /api/v1/plugin-builds/{build_id}/disable
```

`POST /plugins/install` 返回 `202 Accepted` 和 `build_id`；调用方轮询 Build 详情直至
`READY` 或 `FAILED`。Hub 不自动启用新 Build。

安全解包拒绝绝对路径、`..`、重复条目、未列入 checksums 的文件、过大条目及指向根外
的符号链接。Conda 环境如存在合法内部符号链接，目标必须经解析后仍在其环境根目录中。

## 6. Job 创建、状态与执行

调用方用 `runtime_type` 选择 Build；缺省为 `docker`。目标平台对应 Build 未安装、未
就绪或未启用时返回稳定错误，不会回退。

```json
{
  "plugin_id": "nc_to_shp",
  "version": "1.0.0",
  "runtime_type": "docker",
  "inputs": {"source_nc": "file_a1b2c3"},
  "params": {
    "group_name": "1",
    "metrics": ["depth", "stage"],
    "start_time": 1,
    "end_time": 20,
    "target_crs": null
  }
}
```

统一状态机：

```text
PENDING → PREPARING → RUNNING
                         ├→ SUCCESS
                         ├→ FAILED
                         ├→ CANCELLED
                         └→ TIMED_OUT
```

取消是 `cancel_requested=true` 的独立标记；Runner 在 `PREPARING` 或 `RUNNING` 中
观察该标记并优雅停止，必要时强制终止。`TIMED_OUT` 为终态，`result.json` 的终态为
`TIMED_OUT` 并携带 `JOB_TIMED_OUT` 错误码。

创建 Job 时 Hub 校验参数、输入 File、选定 Build 和资源上限，复制或硬链接输入到
不可变 Job 目录，写入严格校验的 `job.json`。Runner 以相同 Job Runtime Protocol
启动两种运行时：

```text
conda-pack：<environment>/bin/python -m hub_runner <job.json> <plugin source>
docker：docker run --rm --network none ... <plugin image digest>
```

无论运行时，插件都读取受控 `/input` 和 `/job/job.json`，只向 `/output` 写最终文件，
并通过标准 Runner Event Protocol 输出进度和日志。Runner 负责原子生成 `result.json`、
验证输出相对路径和 SHA256、登记 `FileRecord`、更新 Job 状态。

## 7. `nc_to_shp` 插件 V1

插件输入为一个 `source_nc` HDF5/NetCDF 文件。参数如下：

| 参数 | 默认值 | 约束 |
| --- | --- | --- |
| `group_name` | `"1"` | Group 必须存在 |
| `metrics` | `["depth", "stage"]` | 非空，只能选择 `depth`、`stage` |
| `start_time` | `1` | 整数且不小于 1 |
| `end_time` | `20` | 整数且不小于 start_time，不能越界 |
| `target_crs` | `null` | null 或合法 `EPSG:<code>` |

插件校验 Group 中的 `xyzgeo`（优先）或 `xyz`、`cells` 和所选指标维度。它保持现有
业务语义：将 `depth/stage` 面值按 `cells` 拓扑关联平均映射至节点；`9999.0` 与 NaN
为无效值，无有效关联节点输出 `-9999.0`；输入坐标系为 EPSG:4326，可按参数投影。

输出唯一的 `nc_to_shp_result.zip`：

```text
nc_to_shp_result.zip
├── depth_<start>-<end>.shp/.shx/.dbf/.prj
├── stage_<start>-<end>.shp/.shx/.dbf/.prj
└── manifest.json
```

归档只包含所请求指标。`manifest.json` 记录 Group、指标、时间范围、目标 CRS、要素
数量和组件清单。输出为 PointZ Shapefile，字段为 `SMID` 与 `time1...timeN`，字段名
不超过 Shapefile 10 字符限制。

两个运行时都需在自己的环境预装 Python、h5py、NumPy、SciPy、GDAL/OGR 和 PROJ。现有
Windows 专用 `PROJ_LIB` 路径不进入插件源码；Conda/Docker 各自通过标准环境配置提供
PROJ 数据。默认资源为超时 3600 秒、CPU 2、内存 8 GiB。

## 8. API、目录、部署与安全

外部 API 至少包括：

```text
POST /api/v1/files
POST /api/v1/plugins/install
GET  /api/v1/plugins
GET  /api/v1/plugin-builds/{build_id}
POST /api/v1/plugin-builds/{build_id}/enable
POST /api/v1/plugin-builds/{build_id}/disable
POST /api/v1/jobs
GET  /api/v1/jobs/{job_id}
POST /api/v1/jobs/{job_id}/cancel
GET  /api/v1/jobs/{job_id}/logs
GET  /api/v1/jobs/{job_id}/outputs
GET  /api/v1/files/{file_id}/download
```

持久化布局：

```text
/data/
├── db/hub.db
├── uploads/<file_id>/payload
├── plugins/<build_id>/package.pypkg
├── plugins/<build_id>/source/
├── environments/<build_id>/          # 仅 conda-pack 解压环境
└── jobs/<job_id>/{input,work,output,logs,job.json,result.json}
```

`hub-server` 仅发布到 `127.0.0.1:8000`，由宿主机 Nginx 提供 TLS、10 GB 上传限制、
长 Job 代理超时和内网 allow/deny。`hub-conda-runner` 与 `hub-docker-runner` 无端口
发布。SQLite、文件、插件包、环境和 Job 目录必须同属持久卷并进入备份策略。

## 9. 双运行时验收与实施顺序

验收输入为 `20260828213102_生成hdf5结果详情信息.nc`：约 152 MB，Group `1`，
39,464 节点、43,708 面、75 时间步。以相同参数（`depth/stage`，1–20，EPSG:4326）
分别运行 Conda 和 Docker Job。

两次验收必须验证安装、启用、上传、Job、日志、输出下载、重启持久化及失败路径。ZIP
或 Shapefile 二进制哈希不作为等价性依据，因为不同环境可能产生不同元数据。使用
GDAL/OGR 比对以下语义：组件集合、PointZ 图层类型、CRS、39,464 要素、字段定义，
并以 `SMID` 关联做全量坐标和属性值比较；同时记录耗时、峰值内存和 ZIP 大小。

最快且依赖关系清晰的实施顺序：

1. 更新严格 Pydantic 契约、旧设计交叉引用和测试：双 runtime、Build 唯一键、统一状态。
2. 新增 Plugin/Build/Environment/Job/JobFile 迁移、服务层和内部 Runner 领取 API。
3. 实现安全 Conda 安装器和 `hub-conda-runner`，先让 NC Conda Build 跑通。
4. 实现 Docker 安装器和 `hub-docker-runner`，运行相同 NC Docker Build。
5. 实现外部 Plugin/Job API、取消、超时、日志、输出和重启恢复。
6. 实现 NC 插件源码、Conda/Docker 构建脚本、双运行时 OGR 端到端验收。
7. 在 Linux AMD64 测试服务器执行真实 Compose 验收；随后分别构建 ARM64 Conda/Docker
   离线 Build，不改业务源码。

在真实 Linux AMD64 Docker 服务器完成此验收前，不可宣称容器或 Conda 环境部署验证
通过。
