# Python Service Hub

本仓库包含 Python Service Hub V1 的协议基础设施。

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
