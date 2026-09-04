# Python Service Hub

This repository contains the protocol foundation for Python Service Hub V1.

## Package boundaries

- `python-hub-contracts` owns the validated wire contracts for `plugin.yaml`,
  `build.json`, `job.json`, `result.json`, and runner events.
- `python-hub-sdk` is the lightweight API available to plugin code: input and
  output value objects, `PluginResult`, `PluginContext`, and stable plugin
  exceptions. It contains no server, persistence, installation, or process
  execution behavior.

`plugin.yaml` is a platform-independent source manifest. Platform data such as
the operating system, CPU architecture, and packaged runtime belongs in
`build.json`, never in `plugin.yaml`.

## Minimal `plugin.yaml`

```yaml
spec_version: "1.0"
plugin:
  id: example_plugin
  name: Example plugin
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

## Plugin entry point

```python
from python_hub_sdk import InputFile, PluginContext, PluginResult


def run(
    params: dict,
    inputs: dict[str, InputFile | list[InputFile]],
    context: PluginContext,
) -> PluginResult:
    ...
```

## Verification

```text
python -m pytest --cov=python_hub_contracts --cov=python_hub_sdk --cov-report=term-missing
python -m ruff check .
python -m mypy packages
```
