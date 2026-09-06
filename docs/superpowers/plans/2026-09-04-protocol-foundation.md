# Python Service Hub V1 协议基础实施计划

> 面向自主开发代理：按任务实施时，使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，并以复选框记录步骤。

**目标：** 建立可安装的单仓库、Pydantic 协议模型和轻量插件 SDK，作为后续 Python Service Hub V1 组件的稳定基础。

**架构：** 一个 Python 单仓库包含三个可独立安装的包：`python-hub-contracts` 管理序列化的 Pydantic 契约，`python-hub-sdk` 提供插件开发 API，后续的 `hub-runner` 与 `hub-server` 同时消费它们。本阶段不实现 HTTP 服务、数据库、环境安装器或进程执行器，而是先建立经测试的协议边界。

**技术栈：** Python 3.12、Pydantic 2.x、PyYAML、pytest、pytest-cov、Ruff、mypy、Hatchling。

**设计基线：** `../../design/python-service-hub-v1-design.md`

## 全局约束

- 运行时协议版本固定为 `1.0`；拒绝不兼容主版本。
- 插件 ID 使用 `^[a-z][a-z0-9_]{2,63}$`；业务版本使用 SemVer `major.minor.patch`。
- `plugin.yaml` 必须平台无关；OS 和 CPU 架构只能出现在 `build.json`/`PluginBuildManifest`。
- V1 仅支持 `process` 运行时和 `linux/amd64`、`linux/arm64` 目标。
- 协议路径必须是 POSIX 相对路径；拒绝绝对路径、NUL、盘符和 `..` 路径穿越。
- Pydantic 协议模型与未来 SQLAlchemy 数据实体严格分离。
- SDK 不依赖 FastAPI、SQLAlchemy、Redis、Hub 内部文件存储或任何服务端框架。
- 可变默认值必须用 `Field(default_factory=...)`；离线发布前精确锁定生产依赖。
- 每一项生产行为必须先有失败测试。

---

## 文件映射

```text
python-service-hub/
├── .gitignore                             # 忽略环境、缓存和构建产物
├── pyproject.toml                         # 工作区工具和测试配置
├── README.md                              # 包职责和开发命令
├── packages/
│   ├── hub-contracts/
│   │   ├── pyproject.toml
│   │   └── src/python_hub_contracts/
│   │       ├── __init__.py                # 稳定公共导出
│   │       ├── common.py                  # ID、版本、路径、严格模型
│   │       ├── plugin_manifest.py         # plugin.yaml 模型
│   │       ├── build_manifest.py          # build.json 模型
│   │       ├── job_protocol.py            # job.json/result.json 模型
│   │       ├── runner_events.py           # stdout 事件模型和解析器
│   │       └── yaml_io.py                 # 安全 YAML 加载器
│   └── hub-sdk/
│       ├── pyproject.toml
│       └── src/python_hub_sdk/
│           ├── __init__.py                # 插件作者公共 API
│           ├── context.py                 # PluginContext 与路径辅助函数
│           ├── errors.py                  # 稳定异常层次
│           └── result.py                  # InputFile、OutputFile、PluginResult
└── tests/
    ├── contracts/                         # 契约测试
    ├── sdk/                               # SDK 测试
    └── fixtures/                          # 有效的 YAML/JSON 夹具
```

## 任务 1：单仓库与质量门禁

**文件：** 新建 `.gitignore`、根 `pyproject.toml`、`README.md`、两个包的 `pyproject.toml` 和 `__init__.py`，以及 `tests/test_package_imports.py`。

**输入/输出：** 依赖 Python 3.12 与设计基线；输出可导入的 `python_hub_contracts`、`python_hub_sdk`，并使 `pytest`、`ruff check .`、`mypy packages` 可运行。

- [ ] **步骤 1：编写导入失败测试**

```python
def test_public_packages_import() -> None:
    import python_hub_contracts
    import python_hub_sdk

    assert python_hub_contracts.__name__ == "python_hub_contracts"
    assert python_hub_sdk.__name__ == "python_hub_sdk"
```

- [ ] **步骤 2：确认包尚不存在**

运行 `python -m pytest tests/test_package_imports.py -v`，预期因 `ModuleNotFoundError` 失败。

- [ ] **步骤 3：建立工作区与包配置**

根 `pyproject.toml` 声明 Python `>=3.12,<3.13`、Pydantic、PyYAML 和开发依赖 Hatchling、pytest、pytest-cov、Ruff、mypy；pytest 的 `pythonpath` 包含两个 `src` 目录；Ruff 使用 Python 3.12 和 100 列；mypy 为严格模式。两个包均采用 Hatchling 的 `src/` 布局，初始 `__init__.py` 仅含文档字符串和 `__version__ = "0.1.0"`。

`.gitignore` 至少忽略 `.venv/`、缓存目录、`.coverage`、`htmlcov/`、`dist/`、`build/` 和 `*.egg-info/`。

- [ ] **步骤 4：安装并验证**

```text
python -m pip install -e ".[dev]" -e packages/hub-contracts -e packages/hub-sdk
python -m pytest tests/test_package_imports.py -v
python -m ruff check .
python -m mypy packages
```

预期测试通过、静态检查退出码为 0；提交信息：`build: scaffold hub protocol workspace`。

## 任务 2：公共契约与安全相对路径

**文件：** 新建 `packages/hub-contracts/src/python_hub_contracts/common.py`，修改包导出，新增 `tests/contracts/test_common.py`。

**输出：** `StrictContractModel`、`PluginId`、`SemanticVersion`、`RelativeProtocolPath`、`normalize_os()`、`normalize_arch()`。

- [ ] **步骤 1：先编写失败测试**

验证 `nc_to_shp` 和 `model_2d_export` 是有效 ID；`NC_TO_SHP`、`nc-to-shp`、中文和过短 ID 被拒绝；`/tmp/a`、`C:/temp/a`、`../a`、`input/../secret` 被路径校验拒绝；额外字段被拒绝；`Linux`、`x86_64`、`aarch64` 分别规范化为 `linux`、`amd64`、`arm64`。

- [ ] **步骤 2：实现严格类型**

`StrictContractModel` 使用 `ConfigDict(extra="forbid", frozen=True)`。路径通过 Pydantic `BeforeValidator` 标准化：必须非空、非绝对、不含 `.`/`..` 段、NUL 或 Windows 盘符。平台函数接受大小写无关别名，但只返回 `Literal["linux"]` 与 `Literal["amd64", "arm64"]`；未知值抛出 `ValueError`。

- [ ] **步骤 3：验证并提交**

```text
python -m pytest tests/contracts/test_common.py -v
python -m ruff check packages/hub-contracts tests/contracts/test_common.py
python -m mypy packages/hub-contracts/src
```

提交：`feat(contracts): add strict protocol primitives`。

## 任务 3：插件与平台构建清单

**文件：** 新建 `plugin_manifest.py`、`build_manifest.py`、`yaml_io.py`；修改公共导出；新增三个清单测试及 `valid-plugin.yaml`、`valid-build.json` 夹具。

**输出：** `PluginManifest`、`PluginBuildManifest`、`load_plugin_manifest(path: Path) -> PluginManifest`。

- [ ] **步骤 1：编写清单负向测试**

覆盖设计中的 `nc_to_shp` 清单；拒绝重复参数/输入/输出名称、未知参数或运行时类型、空或重复 enum 选项、错误 enum 默认值、必填字段的矛盾默认值、错误数值范围、非正文件大小/计数、非 Linux AMD64/ARM64 目标、不安全环境路径和不合法 SHA256。验证 YAML 拒绝锚点/别名和重复映射键。

- [ ] **步骤 2：实现 `PluginManifest`**

实现冻结、禁止额外字段的 `PluginInfo`、`SdkSpec`、`PythonSpec`、`EnvironmentDeclaration`、`RuntimeSpec`、`EntryPointSpec`、`ParameterSpec`、`InputSpec`、`OutputSpec`、`ExecutionSpec`、`EnvironmentVariablesSpec`、`HealthcheckSpec` 和 `PluginManifest`。提供 `parameter_by_name`、`input_by_name`、`output_by_name`；找不到对象时抛出 `KeyError`。

- [ ] **步骤 3：实现 `PluginBuildManifest` 与安全 YAML 加载**

实现 `TargetPlatform`、`PackagedRuntime`、`PluginBuildManifest`。`assert_matches_plugin()` 在插件 ID/版本、Python 版本、SDK 主版本不一致时抛出 `ValueError`。YAML 以 UTF-8 读取，默认 1 MiB，使用安全加载器、拒绝锚点/别名和重复键，只接受映射根对象，并调用 `PluginManifest.model_validate()`。

- [ ] **步骤 4：验证并提交**

```text
python -m pytest tests/contracts/test_plugin_manifest.py tests/contracts/test_build_manifest.py tests/contracts/test_yaml_io.py -v
python -m ruff check packages/hub-contracts tests/contracts
python -m mypy packages/hub-contracts/src
```

提交：`feat(contracts): define plugin and build manifests`。

## 任务 4：Job 运行时和 Runner 事件协议

**文件：** 新建 `job_protocol.py`、`runner_events.py`；修改公共导出；新增 `test_job_protocol.py`、`test_runner_events.py`。

**输出：** `JobRuntimeSpec`、`JobResult`、`ProgressEvent`、`LogEvent` 和 `parse_runner_line(line: str) -> RunnerEvent | None`。

- [ ] **步骤 1：编写 `job.json`/`result.json` 失败测试**

验证协议版本 `1.0`、带时区时间、输入 SHA256、正文件大小、安全路径、超时；`SUCCESS` 必须无错误，`FAILED` 必须有错误且不能含未登记输出，`CANCELLED` 可带稳定取消错误；状态和进度使用大写枚举字符串；`model_dump_json()`/`model_validate_json()` 往返无损。运行时输入类型为 `RuntimeInputFile | list[RuntimeInputFile]`，列表不可为空。

- [ ] **步骤 2：实现模型和事件解析器**

定义 `JobStatus`、`FileRole` 及必要嵌套模型，并使用 `model_validator(mode="after")` 实现跨字段不变式。事件前缀为 `RUNNER_EVENT_PREFIX = "@@HUB@@"`，用 `type` 判别联合；普通 stdout 返回 `None`，有前缀但非法的数据只抛出 `RunnerEventParseError(code="RUNNER_EVENT_INVALID", message=...)`。

- [ ] **步骤 3：验证并提交**

```text
python -m pytest tests/contracts/test_job_protocol.py tests/contracts/test_runner_events.py -v
python -m ruff check packages/hub-contracts tests/contracts
python -m mypy packages/hub-contracts/src
```

提交：`feat(contracts): add job and runner event protocols`。

## 任务 5：插件 SDK 结果与异常

**文件：** 新建 `result.py`、`errors.py`；修改 SDK 公共导出；新增 `test_result.py`、`test_errors.py`。

**输出：** `InputFile`、`OutputFile`、`PluginResult`、`PluginError`、`PluginValidationError`、`PluginExecutionError`、`PluginCancelledError`。

- [ ] **步骤 1：编写失败测试**

验证 `InputFile` 不可变，`OutputFile.path` 拒绝绝对路径和路径穿越，`PluginResult` 不共享可变默认值，结果数据必须为严格 JSON 值。错误码匹配 `^[A-Z][A-Z0-9_]{2,63}$`；`PluginCancelledError()` 默认错误码/消息为 `PLUGIN_CANCELLED` / `任务已取消`。

- [ ] **步骤 2：实现不可变 SDK 值对象和异常层次**

使用冻结、带 slots 的 dataclass，并在 `__post_init__` 校验。仅使用标准库；不得引用 Hub 内部代码。`PluginError` 保存 `code`、`message` 和可选 JSON 安全 `details`；派生类型只改变语义与默认值，绝不保存数据库对象或 HTTP 状态。

- [ ] **步骤 3：验证并提交**

```text
python -m pytest tests/sdk/test_result.py tests/sdk/test_errors.py -v
python -m ruff check packages/hub-sdk tests/sdk
python -m mypy packages/hub-sdk/src
```

提交：`feat(sdk): add plugin result and error contracts`。

## 任务 6：插件上下文、事件与路径安全

**文件：** 新建 `context.py`；修改 SDK 导出；新增 `tests/sdk/test_context.py`。

**输出：** `PluginContext`、`PluginLogger` 协议、`EventSink` 协议、安全的 `output_file()`/`work_file()`。

- [ ] **步骤 1：编写失败测试**

验证 `progress(0)` 与 `progress(100, "done")` 事件、0–100 边界、注入的取消探针、`check_cancelled()`、位于 output 根下的 `output_file("result.zip")`、绝对路径/`..`/符号链接逃逸拒绝，以及仅在 `create_parent=True` 时创建父目录。

- [ ] **步骤 2：实现 `PluginContext`**

```python
PluginContext(
    *, job_id: str, plugin_id: str, plugin_version: str,
    input_dir: Path, work_dir: Path, output_dir: Path,
    logger: PluginLogger, event_sink: EventSink,
    cancellation_probe: Callable[[], bool],
) -> None
```

`progress()` 委托给 `event_sink.emit_progress(percent, message)`。路径辅助函数通过解析根目录和候选路径、`Path.relative_to()` 与真实路径边界证明包含关系，并拒绝绝对输入和符号链接逃逸。

- [ ] **步骤 3：验证并提交**

```text
python -m pytest tests/sdk/test_context.py -v
python -m ruff check packages/hub-sdk tests/sdk
python -m mypy packages/hub-sdk/src
```

提交：`feat(sdk): add plugin execution context`。

## 任务 7：跨包契约验证与阶段验收

**文件：** 修改 `README.md`，新建 `tests/test_contract_examples.py`。

**输入/输出：** 消费任务 1–6 的全部公共契约和 SDK 类型；产出供 Runner 和 Server 后续阶段使用的协议基础候选版本。

- [ ] **步骤 1：端到端契约测试**

加载 `valid-plugin.yaml`，校验 `valid-build.json`，调用 `assert_matches_plugin()`，构造 `JobRuntimeSpec` 并 JSON 往返，构造 `PluginResult` 和成功 `JobResult`，解析一个进度事件。断言 ID、版本、路径与状态贯穿链路不变。

- [ ] **步骤 2：仅补充缺失公共转换**

只有测试确实需要时才添加 `OutputFile.to_protocol_dict()` 等显式转换。不得在此阶段引入 Runner、数据库、HTTP、归档安装或子进程执行代码。

- [ ] **步骤 3：更新 README 并执行完整验证**

README 必须包含最小 `plugin.yaml`、SDK `run()` 签名、验证命令，以及“平台信息属于 `build.json`”的明确说明。

```text
python -m pytest --cov=python_hub_contracts --cov=python_hub_sdk --cov-report=term-missing
python -m ruff check .
python -m mypy packages
```

合并覆盖率至少 90%，其余命令均须零错误。

- [ ] **步骤 4：搜索设计边界并提交**

```text
rg -n "fastapi|sqlalchemy|redis|subprocess" packages/hub-sdk/src
rg -n "(^|[^a-z])targets:" tests/fixtures/valid-plugin.yaml packages/hub-contracts/src
```

预期均无匹配；提交：`test: verify protocol foundation end to end`。

## 阶段完成门禁

- 所有测试通过，合并覆盖率至少 90%；Ruff 和严格 mypy 通过。
- 两个包均能构建为 wheel，设计示例无需修改即可校验。
- 非法 ID、版本、路径、清单、结果不变式和事件均有负向测试。
- `plugin.yaml` 不含目标架构；SDK 不含服务端/框架依赖。
- 本阶段未泄漏 HTTP、ORM、子进程执行器或环境安装器行为。

通过本门禁后，按顺序实施：

1. 持久化和文件系统基础；
2. Runner 与 ProcessExecutor；
3. Plugin/Environment 安全安装；
4. REST API V1；
5. `nc_to_shp` 参考插件；
6. 多架构 Docker 与离线发布。
