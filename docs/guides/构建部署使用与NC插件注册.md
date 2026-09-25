# Python Service Hub：构建、部署、使用与 NC 插件注册指南

> ⚠️ **历史文档（旧拓扑），当前流程以[《插件开发者指南》](插件开发者指南.md)与
> [《CI-CD-GitHub流水线说明》](CI-CD-GitHub流水线说明.md)为准。**
>
> 本文描述 hub / hub-conda-runner / hub-docker-runner 三个独立 Compose 服务的旧部署
> 方式，且写作时 `hub-server` 尚未实现——该前提已过时：单容器拓扑（含插件安装器、
> 文件/作业 REST API、认证、配额等）均已上线，`.pypkg` 可实际安装执行。本文仅作
> 历史参考；当前部署见[单容器部署与脚本插件使用](单容器部署与脚本插件使用.md)，
> 插件开发流程见[插件开发者指南](插件开发者指南.md)。

## 1. 适用范围与当前状态

> ⚠️ 本节描述的"尚未实现"阶段是历史状态：`hub-server`、插件安装器、文件 API 与
> REST API 现已全部实现并上线。第 4 节的平台使用流程与 API 路径，请以
> [插件开发者指南](插件开发者指南.md) §6.4/§6.5 的现行版本为准。

本文分为两个层次：

1. **协议基础。** 本仓库提供 `python-hub-contracts`、`python-hub-sdk`（以及
   `hub-publisher`、`hub-runner`、`hub-server`），可构建 wheel、校验
   `plugin.yaml`/`build.json`、验证 Job/Runner 协议并供插件代码引用。
2. **完整 Hub V1 目标流程。** ~~`hub-server`、`hub-runner`、`ProcessExecutor`、插件安装器、文件 API 和 REST API 尚未在当前分支实现~~（已过时：均已实现）。本文对应步骤以“完整 V1 实现后”标注，严格遵循 [V1 设计基线](../design/python-service-hub-v1-design.md)。

~~当前阶段不提供 `pip install` 后直接启动的 Hub HTTP 服务，也不能实际安装或执行 `.pypkg`。~~（已过时：Hub 服务与 `.pypkg` 安装执行均已可用。）

## 2. 当前仓库：本地构建与验证

### 2.1 前置条件

- Python 3.12（`>=3.12,<3.13`）；
- 支持虚拟环境和 pip 的 Linux、macOS 或 Windows 开发环境；
- 完整 Hub V1 的目标运行平台为 Linux AMD64 或 Linux ARM64。

在仓库根目录执行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate              # Windows PowerShell：.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]" -e packages/hub-contracts -e packages/hub-sdk
```

执行质量门禁：

```bash
python -m pytest --cov=python_hub_contracts --cov=python_hub_sdk --cov-report=term-missing
python -m ruff check .
python -m mypy packages
```

构建两个可分发 wheel：

```bash
cd packages/hub-contracts
python -m hatchling build -t wheel -d ../../dist/contracts

cd ../hub-sdk
python -m hatchling build -t wheel -d ../../dist/sdk
```

构建产物分别位于 `dist/contracts/` 与 `dist/sdk/`。插件环境应安装这两个 wheel；其中 contracts wheel 会声明 Pydantic 和 PyYAML 运行依赖，SDK 保持轻量。

## 3. 完整 V1：Linux AMD64 开发与 Linux ARM64 离线生产

### 3.1 不变原则

| 项目 | AMD64 开发服务器 | ARM64 离线生产服务器 |
|---|---|---|
| Hub 源码、API、数据库 Schema | 同一版本 | 同一版本 |
| 插件源码、`plugin.yaml` | 同一份 | 同一份 |
| Hub 镜像 | `linux-amd64` | `linux-arm64` |
| 插件运行环境 | 独立 AMD64 环境 | 独立 ARM64 环境 |
| `.pypkg` | AMD64 包 | ARM64 包 |

二进制依赖（例如 NumPy、GDAL、Rasterio、netCDF4、h5py）绝不能从 AMD64 复制到 ARM64。插件发布者必须在目标架构或兼容构建环境中自行预构建并验证环境；Hub 只校验和安装，不联网下载、不解析依赖、不编译源码。

### 3.2 完整 V1 的 Hub 镜像构建

完整 V1 实现后，同一 Dockerfile 分别生成架构明确的镜像：

```bash
docker buildx build --platform linux/amd64 \
  -t python-service-hub:1.0.0-linux-amd64 --load .

docker buildx build --platform linux/arm64 \
  -t python-service-hub:1.0.0-linux-arm64 --load .
```

发布前应在真实 ARM64 Linux 上运行 API、数据库迁移和至少一个包含原生库的插件冒烟测试。联网仓库可以发布 multi-arch manifest；离线交付必须保留架构明确的归档：

```bash
docker save -o python-service-hub-1.0.0-linux-arm64.tar \
  python-service-hub:1.0.0-linux-arm64
sha256sum python-service-hub-1.0.0-linux-arm64.tar > checksums.sha256
```

### 3.3 ARM64 离线部署

交付目录建议如下：

```text
python-service-hub-offline-1.0.0-linux-arm64/
├── images/python-service-hub-1.0.0-linux-arm64.tar
├── docker-compose.yml
├── config/hub.yaml
├── checksums.sha256
└── README.md
```

生产机先离线校验镜像，再加载并启动：

```bash
sha256sum -c checksums.sha256
docker load -i images/python-service-hub-1.0.0-linux-arm64.tar
docker compose up -d
```

完整 V1 的 `hub.yaml` 示例：

```yaml
deployment:
  mode: offline

storage:
  root: /data

database:
  url: sqlite:////data/db/hub.db

executor:
  type: process
  cancel_grace_seconds: 15

uploads:
  max_size_bytes: 10737418240
```

必须把 SQLite 数据库、插件、Environment、Job 工作目录、上传文件、输出与日志挂载到持久卷，并纳入备份。Hub 及插件进程均应以非 root 用户运行；插件和 Environment 默认只读。

## 4. 完整 V1 的平台使用流程

```text
上传 .pypkg → 安全校验并安装 → READY → 显式启用 → ENABLED
上传 .nc 文件 → 创建 Job → PENDING → PREPARING → RUNNING
                                          ├→ SUCCESS（下载输出）
                                          ├→ FAILED
                                          └→ CANCELLED / TIMED_OUT
```

插件安装后默认状态为 `READY`，不会自动启用。只有当前 Linux 平台匹配、Environment 为 `READY` 且 Build 被显式设为 `ENABLED` 时，才能创建 Job。

常用 API 前缀为 `/api/v1`：

```text
POST /plugins/install
POST /plugins/{plugin_id}/{version}/enable
POST /files
POST /jobs
GET  /jobs/{job_id}
GET  /jobs/{job_id}/logs
GET  /jobs/{job_id}/outputs
GET  /files/{file_id}/download
POST /jobs/{job_id}/cancel
```

## 5. 示例：`nc_to_shp` 插件源码

下面以“解析 NetCDF（NC）并输出 Shapefile ZIP”为例。建议的源码目录：

```text
nc_to_shp/
├── plugin.yaml
├── src/
│   ├── __init__.py
│   └── main.py
├── environment.yml
├── runtime/                         # 打包时放入目标架构预构建环境
└── README.md
```

### 5.1 平台无关的 `plugin.yaml`

```yaml
spec_version: "1.0"

plugin:
  id: nc_to_shp
  name: NC 转 Shapefile
  version: "1.0.0"
  description: 将水动力模型 NetCDF 结果转换为 Shapefile ZIP
  category: gis
  tags: [netcdf, gis, water]

sdk:
  version: "1.0"

runtime:
  type: process
  python:
    version: "3.12"
  environment:
    type: conda
    file: environment.yml

entrypoint:
  module: src.main
  function: run

parameters:
  - name: start_time
    label: 开始时刻
    type: integer
    required: false
    default: 1
    min: 1
  - name: end_time
    label: 结束时刻
    type: integer
    required: false
  - name: target_epsg
    label: 输出坐标系
    type: integer
    required: false
    default: 3857
  - name: method
    label: 插值方法
    type: enum
    required: false
    default: nearest
    options: [nearest, linear, cubic]

inputs:
  - name: nc_file
    label: NC 结果文件
    type: file
    required: true
    extensions: [.nc, .h5, .hdf5]
    max_size: 10737418240

outputs:
  - name: result_files
    label: 输出文件
    type: files
    required: true

execution:
  timeout: 3600
  concurrency: 1

environment_variables:
  required: []

healthcheck:
  enabled: true
  type: import
```

禁止在此文件中添加 `targets`、架构、绝对部署路径、密码、Token 或连接凭据。

### 5.2 插件入口函数

以下代码展示 SDK 契约的使用方式；实际 NC 解析可使用 `xarray`、`netCDF4`、`geopandas` 等已预装在插件环境内的库。

```python
from pathlib import Path

from python_hub_sdk import InputFile, OutputFile, PluginContext, PluginResult


def run(
    params: dict,
    inputs: dict[str, InputFile | list[InputFile]],
    context: PluginContext,
) -> PluginResult:
    nc_file = inputs["nc_file"]
    assert isinstance(nc_file, InputFile)

    context.logger.info("开始处理 NC 文件：%s", nc_file.name)
    context.progress(5, "正在读取 NetCDF 元数据")
    context.check_cancelled()

    # 在此读取 nc_file.path，按 params 转换数据，并写入 output 根目录。
    result_zip: Path = context.output_file("nc_to_shp_result.zip", create_parent=True)
    # build_shapefile_zip(nc_file.path, result_zip, params, context)

    context.check_cancelled()
    context.progress(100, "转换完成")
    return PluginResult(
        files=[
            OutputFile(
                name="NC 转 Shapefile 结果",
                path="nc_to_shp_result.zip",
                format="zip",
            )
        ]
    )
```

插件只能使用 `input_dir`、`work_dir`、`output_dir` 中由 `PluginContext` 暴露的路径；不得写入 Hub 数据库、宿主敏感目录或协议外的绝对路径。预期的业务校验应抛出 SDK 的 `PluginValidationError`，而不是泄露 HTTP 状态或数据库细节。

## 6. 预构建插件环境与生成 `.pypkg`

以下流程必须分别在 AMD64 与 ARM64 上执行。以 ARM64 生产包为例：

```bash
# 仅在 linux/arm64 构建机或真实 ARM64 机器上执行
micromamba create -y -p ./build-env python=3.12 xarray netcdf4 geopandas pyogrio
./build-env/bin/python -m pip install \
  dist/contracts/python_hub_contracts-0.1.0-py3-none-any.whl \
  dist/sdk/python_hub_sdk-0.1.0-py3-none-any.whl

# 运行插件健康检查和代表性 NC 任务后，再打包环境
conda-pack -p ./build-env -o runtime/env.tar
zstd -19 runtime/env.tar -o runtime/env.tar.zst
sha256sum runtime/env.tar.zst
```

生成同一源码的 AMD64 包时，必须在 AMD64 上重复以上步骤，不能复用 ARM64 环境包。

`build.json` 仅描述本次平台 Build：

```json
{
  "schema_version": "1.0",
  "build_id": "build_01NCARM6400000000000001",
  "plugin_id": "nc_to_shp",
  "plugin_version": "1.0.0",
  "target": {"os": "linux", "arch": "arm64"},
  "runtime": {
    "python_version": "3.12",
    "environment_format": "conda-pack",
    "environment_path": "runtime/env.tar.zst",
    "environment_sha256": "<env.tar.zst 的 64 位小写 SHA256>"
  },
  "sdk_version": "1.0.0",
  "source_sha256": "<源码内容的 64 位小写 SHA256>",
  "built_at": "2026-09-06T12:00:00Z"
}
```

打包前写入完整性清单：

```bash
sha256sum plugin.yaml build.json src/main.py runtime/env.tar.zst > checksums.sha256
zip -r nc_to_shp-1.0.0-linux-arm64.pypkg \
  plugin.yaml build.json src runtime README.md checksums.sha256
sha256sum nc_to_shp-1.0.0-linux-arm64.pypkg
```

容器布局必须如下：

```text
nc_to_shp-1.0.0-linux-arm64.pypkg
├── plugin.yaml
├── build.json
├── src/
├── runtime/env.tar.zst
├── README.md
└── checksums.sha256
```

## 7. 完整 V1：注册、启用和调用 NC 插件

以下命令需要完整 V1 的 Server/API 已实现且 Hub 本机为 Linux ARM64。若在 AMD64 Hub 上传 ARM64 包，平台必须拒绝并返回 `PLATFORM_MISMATCH`。

### 7.1 安装并启用

```bash
curl --fail-with-body -X POST http://hub.example.internal:8000/api/v1/plugins/install \
  -F "file=@nc_to_shp-1.0.0-linux-arm64.pypkg"

curl --fail-with-body -X POST \
  http://hub.example.internal:8000/api/v1/plugins/nc_to_shp/1.0.0/enable
```

服务端的安装链路是：接收文件 → SHA256 → 安全解压到临时目录 → Pydantic 校验两个清单 → 校验平台、版本、SDK 和 Environment → 原子安装 → 健康检查 → 数据库事务注册为 `READY`。显式启用后才成为 `ENABLED`。

### 7.2 上传 NC 输入文件

```bash
curl --fail-with-body -X POST http://hub.example.internal:8000/api/v1/files \
  -F "file=@model.nc"
```

记下响应中的 `file_id`，例如 `file_01NCINPUT00000000000001`。上传与建任务分离，因此可重试上传、复用文件，并在未来迁移到对象存储。

### 7.3 创建异步 Job

```bash
curl --fail-with-body -X POST http://hub.example.internal:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "plugin_id": "nc_to_shp",
    "version": "1.0.0",
    "params": {
      "start_time": 1,
      "end_time": 24,
      "target_epsg": 3857,
      "method": "nearest"
    },
    "inputs": {"nc_file": "file_01NCINPUT00000000000001"}
  }'
```

Job 创建成功后立即返回 HTTP 201 和 `PENDING`。Hub 会固化精确插件版本、Build、参数、输入文件元数据和 SHA256；随后由 ProcessExecutor 使用该 Build 的环境 Python 启动 Runner。

### 7.4 查询进度、日志和输出

```bash
curl http://hub.example.internal:8000/api/v1/jobs/job_01NCJOB0000000000000001
curl "http://hub.example.internal:8000/api/v1/jobs/job_01NCJOB0000000000000001/logs?tail=200"
curl http://hub.example.internal:8000/api/v1/jobs/job_01NCJOB0000000000000001/outputs
curl -OJ http://hub.example.internal:8000/api/v1/files/file_01NCOUTPUT0000000000001/download
```

取消是异步请求：

```bash
curl --fail-with-body -X POST \
  http://hub.example.internal:8000/api/v1/jobs/job_01NCJOB0000000000000001/cancel
```

取消响应仅代表请求已接收；只有进程组终止、Runner 写入最终结果且状态机完成转换后，Job 才会变为 `CANCELLED`。超时由 `execution.timeout` 强制执行，最终状态为 `TIMED_OUT`。

## 8. 运行与发布检查表

- [ ] Hub 镜像、插件 `.pypkg` 和 `checksums.sha256` 的架构均为目标平台。
- [ ] 已在真实 ARM64 Linux 上验证 ARM64 镜像、环境和代表性 NC 文件。
- [ ] `plugin.yaml` 不含 `targets`、密钥、绝对路径或平台专用二进制包。
- [ ] `build.json` 的 OS/架构、Python、SDK 版本、环境 SHA256 与实际产物一致。
- [ ] 生产 Hub 离线运行；缺少环境包时返回 `RUNTIME_PACKAGE_REQUIRED`，绝不联网安装依赖。
- [ ] 插件运行用户非 root；插件和 Environment 只读；Job 仅能写 `work/`、`output/`、`logs/`。
- [ ] 已备份数据库和持久化数据目录，并记录 Job → PluginBuild → Environment → 输入/输出 SHA256。
