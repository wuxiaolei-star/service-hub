# Python Service Hub

本仓库包含 Python Service Hub V1 的协议基础设施及可部署的服务基础。

## 包职责边界

- `python-hub-contracts` 负责经过校验的线协议契约：`plugin.yaml`、
  `build.json`、`job.json`、`result.json` 与 Runner 事件。
- `python-hub-sdk` 是提供给插件代码的轻量 API：输入/输出值对象、
  `PluginResult`、`PluginContext` 与稳定的插件异常类型。它不包含服务端、
  持久化、安装或进程执行行为。

`plugin.yaml` 是平台无关的源码清单。操作系统、CPU 架构和已打包运行环境等
平台信息必须位于 `build.json`，绝不能写入 `plugin.yaml`。

## 最小 `plugin.yaml` 示例

```yaml
spec_version: "1.0"
plugin:
  id: example_plugin
  name: 示例插件
  version: "1.0.0"
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
parameters: []
inputs: []
outputs: []
execution:
  timeout: 3600
  concurrency: 1
environment_variables:
  required: []
healthcheck:
  enabled: true
  type: import
```

## 插件入口函数

```python
from python_hub_sdk import InputFile, PluginContext, PluginResult


def run(
    params: dict,
    inputs: dict[str, InputFile | list[InputFile]],
    context: PluginContext,
) -> PluginResult:
    ...
```

## 验证命令

```text
python -m pytest --cov=python_hub_contracts --cov=python_hub_sdk --cov-report=term-missing
python -m ruff check .
python -m mypy packages
```

## Linux AMD64 部署

当前镜像只提供系统和文件 API；插件安装、运行环境管理、Job 和 Runner 执行仍属于
后续阶段。Hub 本身没有应用层认证，必须只部署在 Nginx、主机防火墙和内网边界之后。

在目标 Linux AMD64 主机将**完整仓库检出**放在 `/srv/python-service-hub`。该检出必须包含
`Dockerfile`、`alembic/`、`alembic.ini`、`packages/hub-server/`、`compose.yaml` 和 `config/`；
Compose 的 `build: .` 需要这些文件作为构建上下文。然后准备持久化目录和配置：

```bash
sudo git clone <approved-repository-url> /srv/python-service-hub
cd /srv/python-service-hub
sudo install -d -m 0750 /srv/python-service-hub/{config,data}
sudo cp config/hub.yaml.example /srv/python-service-hub/config/hub.yaml
sudo docker compose up -d --build
curl http://127.0.0.1:8000/api/v1/system/health
```

`data/` 保存 SQLite 数据库和上传文件；备份或恢复时应整体处理该目录。Compose 只将
Hub 发布到 `127.0.0.1:8000`，不对公网开放。将
`deploy/nginx/python-service-hub.conf.example` 安装到宿主机 Nginx 后，替换其中的域名、
证书路径以及 `allow` 网段，再由 Nginx 通过 HTTPS 代理内网请求。

可在 Linux AMD64 Docker 主机执行以下部署 smoke 测试。它使用随机 Compose 项目名、临时
数据目录、临时 loopback 端口和独立 Compose 文件，并在 `finally` 中只停止该随机项目：

```text
python -m pytest -m integration tests/server/test_container_smoke.py -v
```

不要在生产目录执行手工 `docker compose down --volumes`，以免停止正在使用的服务。
