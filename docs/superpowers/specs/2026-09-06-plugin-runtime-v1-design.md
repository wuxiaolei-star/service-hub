# Python Service Hub V1：Docker 插件运行时与 NC 转 Shapefile 设计

## 1. 目标、范围与硬性约束

本设计在现有 Service Hub 服务基础之上，实现可注册、可启动的 Docker
批处理插件。V1 的首个插件为 `nc_to_shp`：读取指定结构的 NC/HDF5 文件，将
`depth` 和/或 `stage` 面值映射为节点 PointZ Shapefile，并以一个 ZIP 文件返回。

V1 必须满足：

- 外部系统只调用 `hub-server` 的 REST API；不直接调用插件或 Runner。
- 插件发布者自行构建目标平台 Docker 运行环境。Hub 不执行 `pip install`、
  `conda install`、GDAL 编译或依赖解析。
- Hub 在 Linux AMD64 测试服务器先完成端到端验证；未来 Linux ARM64 离线生产
  服务器使用同一 Hub/插件源码的 ARM64 独立构建产物。
- `nc_to_shp` 是一次性批处理插件：一个 Job 启动一个临时容器，完成后退出。
- V1 采用一个 Runner Worker 和每插件并发数 1；不实现消息队列、WebSocket、
  插件常驻 HTTP 服务或应用层认证。
- 认证暂缓；部署必须依赖 Nginx TLS、内网来源限制和主机防火墙保护。

现有 Hub 基础服务已经提供系统信息、文件上传/下载、SQLite、Compose 与 Nginx
示例。本设计新增插件注册、镜像导入、Job 执行和 NC 插件垂直闭环。

## 2. 总体架构

```text
外部业务系统
      │ HTTPS
      ▼
    Nginx
      │
      ▼
hub-server ─────────────── /data（SQLite、文件、插件包、Job 工作目录）
  - REST API                         │
  - Plugin/Build 注册                │
  - File/Job 元数据                  │
  - Job 查询、取消、日志、输出       │
                                    ▼
                              hub-runner
                              - 领取一个 Job
                              - docker load 插件镜像
                              - docker run / stop
                              - 日志、事件、result 回传
                                    │ Docker Socket
                                    ▼
                       nc_to_shp 临时插件容器
                       /input  只读挂载
                       /output 可写挂载
                       /job/job.json 只读挂载
```

`hub-server` 和 `hub-runner` 均是 Service Hub Compose 栈的内部组件。Runner 不对
主机发布端口；只有它挂载 Docker Socket。插件容器永远不挂载 Docker Socket、
不访问 Hub SQLite、默认不联网、不使用 privileged 模式。

### 2.1 组件职责

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| `hub-server` | REST API、清单校验、元数据、文件、Job 生命周期、查询 | 构建依赖、调用 Docker、执行 GDAL |
| `hub-runner` | 导入已验证镜像、领取 Job、启动/停止容器、资源限制、日志与结果回传 | 外部 API、插件依赖安装、业务计算 |
| `python-hub-contracts` | `plugin.yaml`、`build.json`、`job.json`、`result.json`、Runner 事件模型 | FastAPI、数据库、Docker SDK |
| `python-hub-sdk` | 插件入口的输入、输出、上下文和异常值对象 | Hub 服务端与容器编排 |
| `nc_to_shp` | NC/HDF5 解析、面值到节点映射、Shapefile/ZIP 输出 | Hub API、数据库、Docker 编排 |

## 3. Docker 插件构建与离线包

插件源码清单不绑定 CPU 架构；Build 包才绑定平台。每个目标平台必须独立构建。

```text
nc_to_shp 源码
  ├─ linux/amd64 → nc_to_shp-1.0.0-linux-amd64.pypkg
  └─ linux/arm64 → nc_to_shp-1.0.0-linux-arm64.pypkg
```

V1 插件包格式：

```text
nc_to_shp-1.0.0-linux-amd64.pypkg
├── plugin.yaml
├── build.json
├── image.tar.zst
└── checksums.json
```

`image.tar.zst` 是发布者预构建的 Docker 镜像离线归档。插件注册时，Hub 解包并
校验所有文件 SHA256；Runner 以 `docker load` 导入镜像。运行时只使用 Build 中记录
的不可变 image digest，不按可变 tag 解析。任何校验、解包、导入或镜像摘要验证失败
均使 Build 停留在 `FAILED`，不能启用。

`plugin.yaml` 描述平台无关能力；`build.json` 最少声明：

```json
{
  "plugin_id": "nc_to_shp",
  "plugin_version": "1.0.0",
  "target": {"os": "linux", "arch": "amd64"},
  "runtime": {
    "type": "docker",
    "image": "nc_to_shp:1.0.0-linux-amd64",
    "digest": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "archive": "image.tar.zst"
  }
}
```

镜像需包含 Python、h5py、NumPy、SciPy、GDAL/OGR 和 PROJ。容器内不得使用现有
脚本中 Windows 专用的 `PROJ_LIB` 路径；应使用镜像中 GDAL/PROJ 的标准数据目录。

## 4. 插件注册与版本生命周期

```text
上传 .pypkg
  → Hub 安全解包、验证 manifests/checksums/platform
  → PluginBuild=INSTALLING
  → Runner docker load + digest 校验
  → PluginBuild=READY
  → 显式 enable
  → PluginBuild=ENABLED
  → 可创建 Job
```

同一 `(plugin_id, version, os, arch)` Build 不可覆盖。新的插件镜像或依赖变更必须
发布新版本或新 Build；已成功 Job 永远记录实际 `PluginBuild` 和 image digest。

安装 API：

```http
POST /api/v1/plugins/install
Content-Type: multipart/form-data

file=<pypkg>
```

响应至少包含 `plugin_id`、`version`、`build_id`、目标平台与安装状态。启用与禁用
使用显式 API；V1 不自动启用新安装 Build。

## 5. 数据模型

现有 `FileRecord` 继续保存文件元数据和 `/data` 下相对路径。新增模型：

```text
Plugin 1 ── * PluginVersion 1 ── * PluginBuild 1 ── 1 Environment
                                           │
                                           *
                                           │
                                          Job * ── * FileRecord
```

- `Plugin`：稳定插件 ID、名称、描述、类别。
- `PluginVersion`：不可变语义版本及平台无关 `plugin.yaml` 快照。
- `PluginBuild`：目标 OS/架构、构建清单、包 SHA256、image digest、状态、失败原因。
- `Environment`：Docker 镜像引用、导入时间、digest、镜像大小和可用状态；不是 Python
  虚拟环境，不包含由 Hub 管理的包安装步骤。
- `Job`：所用 Build、请求参数、状态、取消/超时信息、开始/结束时间、退出码、错误。
- `JobFile`：Job 输入和输出与 `FileRecord` 的关联、名称和角色。

所有状态改变在 SQLite 事务中完成；运行日志保存为 `/data/jobs/<job_id>/logs/runner.log`
及结构化事件，数据库保存可查询的摘要和游标。

## 6. Job API、协议与状态机

V1 外部 API：

```text
POST /api/v1/files
POST /api/v1/plugins/install
GET  /api/v1/plugins
POST /api/v1/plugins/{plugin_id}/{version}/enable
POST /api/v1/plugins/{plugin_id}/{version}/disable
POST /api/v1/jobs
GET  /api/v1/jobs/{job_id}
POST /api/v1/jobs/{job_id}/cancel
GET  /api/v1/jobs/{job_id}/logs
GET  /api/v1/jobs/{job_id}/outputs
GET  /api/v1/files/{file_id}/download
```

创建 NC Job：

```json
{
  "plugin_id": "nc_to_shp",
  "version": "1.0.0",
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

状态机：

```text
QUEUED → RUNNING → SUCCEEDED
                  ├→ FAILED
                  ├→ TIMED_OUT
                  └→ CANCELLED

QUEUED/RUNNING → CANCEL_REQUESTED → CANCELLED
```

Runner 仅领取 `QUEUED` Job，以原子状态转移改为 `RUNNING`。超时或取消通过停止对应
容器实现。运行容器退出但缺少、无效或不匹配的 `result.json` 时，Job 为 `FAILED`。

插件容器读取 `/job/job.json`，只将最终 ZIP 写入 `/output`，并通过标准 Runner
事件输出进度和日志。Runner 收集退出码、事件、输出元数据后，原子写入
`/data/jobs/<job_id>/result.json`：

```json
{
  "protocol_version": "1.0",
  "job_id": "job_a1b2c3",
  "status": "SUCCESS",
  "started_at": "2026-09-06T13:00:00+08:00",
  "finished_at": "2026-09-06T13:01:20+08:00",
  "duration_ms": 80000,
  "message": "NC 转 Shapefile 完成",
  "data": {},
  "files": [
    {
      "name": "nc_to_shp_result.zip",
      "path": "nc_to_shp_result.zip",
      "format": "zip",
      "size": 123456,
      "sha256": "c3ab8ff13720e8ad9047dd39466b3c897cbe1249f585d3de8c5d2ed7e51f0a8d"
    }
  ],
  "error": null
}
```

Runner 必须校验相对协议路径仍在输出根目录中，验证输出 SHA256 后由 Hub 注册为
`FileRecord`。数据库 Job 的 `SUCCEEDED` 状态对应 `result.json` 的协议状态
`SUCCESS`。调用方轮询 Job 状态，使用输出 File ID 走统一下载 API；V1 不实现
WebSocket 或外部消息队列。

## 7. `nc_to_shp` V1 契约

输入：一个 `source_nc` 文件，必须为 HDF5/NetCDF 可读文件。参数：

| 参数 | 默认值 | 约束 |
| --- | --- | --- |
| `group_name` | `"1"` | 目标 HDF5 Group 必须存在 |
| `metrics` | `["depth", "stage"]` | 非空，元素只能为 `depth` 或 `stage` |
| `start_time` | `1` | 整数，最小为 1 |
| `end_time` | `20` | 整数，且不小于 `start_time` |
| `target_crs` | `null` | null 或可被 GDAL/PROJ 识别的 `EPSG:<code>` |

插件要求 Group 包含 `xyzgeo`（优先）或 `xyz`、`cells`，并要求每个所选指标的
时间/面维度与 `cells` 一致。缺失指标、非法 CRS、时间范围越界、拓扑不一致或 HDF5
读取错误均为清晰的 Job 失败，不允许静默跳过指标。

核心计算保持现有业务语义：按 `cells` 建立节点-面关联矩阵，对每个时间步将有效面值
平均映射至关联节点；`9999.0` 和 NaN 作为无效值，节点没有有效关联值时输出 `-9999.0`。
坐标输入默认为 EPSG:4326，并可转换到 `target_crs`。输出为 PointZ Shapefile。

输出是一个 `nc_to_shp_result.zip`：

```text
nc_to_shp_result.zip
├── depth_<start>-<end>.shp/.shx/.dbf/.prj
├── stage_<start>-<end>.shp/.shx/.dbf/.prj
└── manifest.json
```

仅包含请求的 metrics。`manifest.json` 记录输入文件、Group、指标、时间范围、CRS、
要素数、Shapefile 组件列表和生成时间。DBF 属性字段为 `SMID` 加 `time1` 至
`timeN`；字段名始终不超过 Shapefile 的 10 字符限制。

默认资源声明：超时 3600 秒、CPU 2、内存 8 GiB。Runner 将这些限制映射为 Docker
运行参数，并可在未来按运维策略设置更低的全局上限。

已分析的验收样本 `20260828213102_生成hdf5结果详情信息.nc` 约 152 MB，Group `1`
具有 39,464 个节点、43,708 个面和 75 个时间步；它是 AMD64 端到端验收输入。

## 8. 安全、可复现与错误处理

- 安装包仅可安全解包至 Hub 管理的临时目录；拒绝绝对路径、`..`、符号链接逃逸、
  重复条目、未声明文件和超过上限的归档。
- 所有清单使用 Pydantic 严格模型校验。包、内部文件和 Docker image digest 均验证。
- 插件容器以非 root 用户运行，挂载输入只读、输出限于 Job 目录，默认 `--network none`，
  不授予特权或额外 capabilities。
- API 失败统一为 `{"success": false, "error": {"code", "message", "details"}}`；
  不泄露宿主机路径、堆栈或 Docker Socket 细节。
- 所有 Build、Job 和输出均记录不可变 manifest/digest/参数快照，以支持复现和审计。
- V1 没有应用层认证，因此 Nginx 必须启用 TLS、正确的 `allow` 网段和 `deny all`；
  宿主机防火墙不得公开 8000 端口。

## 9. 部署与跨架构

测试服务器为 Linux AMD64，Hub Compose 显式运行 `linux/amd64`，外部仅访问 Nginx。
开发者可在服务器从完整源码构建 Hub；真实 Docker 启动、上传与 Job smoke 是该服务器
的发布验收门槛。

未来 ARM64 离线生产遵循：

1. 用同一 Hub 源码构建 ARM64 Hub/Runner 镜像。
2. 用同一 `nc_to_shp` 源码构建 ARM64 插件镜像和 ARM64 `.pypkg`。
3. 通过离线介质传输镜像 tar 与插件包；先 `docker load` Hub/Runner 镜像，再由 Hub
   注册插件 Build。
4. 不复用 AMD64 插件镜像，不在生产服务器安装或编译业务依赖。

## 10. 验收与非目标

端到端验收：部署 Hub → 上传样本 NC → 注册 AMD64 `nc_to_shp` Build → 启用 → 创建
depth/stage 1–20 Job → 成功下载 ZIP → 检查两组 Shapefile、CRS、39,464 点和属性字段。
还须覆盖注册失败、镜像摘要不符、超时、取消、错误 `result.json`、无效 NC、非法参数、
重启后 Plugin/Job/File 持久化等测试。

V1 非目标：应用层认证与多租户、插件常驻服务、并行多 Worker、远程容器平台、在线
依赖安装/编译、插件自动升级、WebSocket 和通用 NC 数据模型适配。后续新增插件格式时，
应通过新插件版本/能力声明扩展，不改变 `nc_to_shp` V1 语义。
