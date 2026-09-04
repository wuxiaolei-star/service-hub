# Python Service Hub V1 Protocol Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the installable monorepo foundation, Pydantic protocol models, and lightweight plugin SDK that all later Python Service Hub V1 components depend on.

**Architecture:** Use one Python monorepo with three separately installable packages: `python-hub-contracts` owns serialized Pydantic contracts, `python-hub-sdk` owns the plugin-facing programming API, and later `hub-runner`/`hub-server` consume both. This phase contains no HTTP server, database, environment installer, or process executor; it produces a stable, tested protocol boundary first.

**Tech Stack:** Python 3.12 for development, Pydantic 2.x, PyYAML, pytest, pytest-cov, Ruff, mypy, Hatchling.

**Spec:** `../../design/python-service-hub-v1-design.md`

## Global Constraints

- Runtime protocol version is exactly `1.0`; incompatible major versions are rejected.
- Plugin ID matches `^[a-z][a-z0-9_]{2,63}$`; plugin business versions use SemVer `major.minor.patch`.
- `plugin.yaml` is platform-independent; OS and architecture exist only in `build.json`/`PluginBuildManifest`.
- V1 runtime type is only `process`; supported target values are `linux/amd64` and `linux/arm64`.
- Serialized paths are relative POSIX-style paths; absolute paths and `..` traversal are rejected.
- Pydantic models and future SQLAlchemy entities remain separate.
- Plugin SDK has no FastAPI, SQLAlchemy, Redis, filesystem storage, or Hub-internal dependency.
- Mutable model defaults use `Field(default_factory=...)`.
- Production dependencies are exactly pinned before offline release; this plan initially constrains compatible versions in `pyproject.toml` and generates a lock file during release work.
- Every production behavior is introduced through a failing test first.

---

## File Map

```text
python-service-hub/
├── .gitignore                             # excludes environments, caches and build output
├── pyproject.toml                         # workspace tooling and test configuration
├── README.md                              # development commands and package map
├── packages/
│   ├── hub-contracts/
│   │   ├── pyproject.toml                 # contracts package metadata
│   │   └── src/python_hub_contracts/
│   │       ├── __init__.py                # stable public exports
│   │       ├── common.py                  # shared IDs, versions, paths, strict base model
│   │       ├── plugin_manifest.py         # plugin.yaml models
│   │       ├── build_manifest.py          # build.json models
│   │       ├── job_protocol.py            # job.json/result.json models
│   │       ├── runner_events.py           # stdout event models/parser
│   │       └── yaml_io.py                 # safe YAML-to-model loader
│   └── hub-sdk/
│       ├── pyproject.toml                 # SDK package metadata
│       └── src/python_hub_sdk/
│           ├── __init__.py                # plugin-author public API
│           ├── context.py                 # PluginContext and path helpers
│           ├── errors.py                  # stable exception hierarchy
│           └── result.py                  # InputFile, OutputFile, PluginResult
└── tests/
    ├── contracts/
    │   ├── test_common.py
    │   ├── test_plugin_manifest.py
    │   ├── test_build_manifest.py
    │   ├── test_job_protocol.py
    │   ├── test_runner_events.py
    │   └── test_yaml_io.py
    ├── sdk/
    │   ├── test_context.py
    │   ├── test_errors.py
    │   └── test_result.py
    └── fixtures/
        ├── valid-plugin.yaml
        └── valid-build.json
```

## Task 1: Monorepo and Quality Gate

**Files:**
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `packages/hub-contracts/pyproject.toml`
- Create: `packages/hub-contracts/src/python_hub_contracts/__init__.py`
- Create: `packages/hub-sdk/pyproject.toml`
- Create: `packages/hub-sdk/src/python_hub_sdk/__init__.py`
- Test: `tests/test_package_imports.py`

**Interfaces:**
- Consumes: Python 3.12 and the design baseline.
- Produces: importable `python_hub_contracts` and `python_hub_sdk` packages; commands `pytest`, `ruff check .`, and `mypy packages`.

- [ ] **Step 1: Write the package import test**

```python
def test_public_packages_import() -> None:
    import python_hub_contracts
    import python_hub_sdk

    assert python_hub_contracts.__name__ == "python_hub_contracts"
    assert python_hub_sdk.__name__ == "python_hub_sdk"
```

- [ ] **Step 2: Run the test and confirm the packages do not exist**

Run: `python -m pytest tests/test_package_imports.py -v`

Expected: collection fails with `ModuleNotFoundError` for `python_hub_contracts`.

- [ ] **Step 3: Add workspace configuration and minimal packages**

Root `pyproject.toml` must contain:

```toml
[project]
name = "python-service-hub-workspace"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "pydantic>=2.9,<3",
  "PyYAML>=6.0,<7",
]

[project.optional-dependencies]
dev = [
  "hatchling>=1.25,<2",
  "mypy>=1.11,<2",
  "pytest>=8.3,<9",
  "pytest-cov>=5,<6",
  "ruff>=0.6,<1",
]

[tool.pytest.ini_options]
addopts = "--strict-markers --strict-config"
testpaths = ["tests"]
pythonpath = [
  "packages/hub-contracts/src",
  "packages/hub-sdk/src",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["python_hub_contracts", "python_hub_sdk"]
mypy_path = ["packages/hub-contracts/src", "packages/hub-sdk/src"]
```

Each package uses Hatchling with a `src/` layout. Its `__init__.py` initially contains only a module docstring and `__version__ = "0.1.0"`.

Create `.gitignore` with `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `htmlcov/`, `dist/`, `build/`, and `*.egg-info/`. Run `git init` in the `python-service-hub` directory before the first commit.

- [ ] **Step 4: Install the workspace and run the package test**

Run: `python -m pip install -e ".[dev]" -e packages/hub-contracts -e packages/hub-sdk`

Run: `python -m pytest tests/test_package_imports.py -v`

Expected: `1 passed`.

- [ ] **Step 5: Run initial static checks**

Run: `python -m ruff check .`

Run: `python -m mypy packages`

Expected: both commands exit 0.

- [ ] **Step 6: Commit the scaffold**

```bash
git add pyproject.toml README.md packages tests/test_package_imports.py
git commit -m "build: scaffold hub protocol workspace"
```

## Task 2: Common Contract Types and Safe Relative Paths

**Files:**
- Create: `packages/hub-contracts/src/python_hub_contracts/common.py`
- Modify: `packages/hub-contracts/src/python_hub_contracts/__init__.py`
- Test: `tests/contracts/test_common.py`

**Interfaces:**
- Consumes: Pydantic `BaseModel`, `ConfigDict`, `StringConstraints`.
- Produces: `StrictContractModel`, `PluginId`, `SemanticVersion`, `RelativeProtocolPath`, `normalize_os()`, `normalize_arch()`.

- [ ] **Step 1: Write failing validation tests**

Tests must prove:

```python
@pytest.mark.parametrize("value", ["nc_to_shp", "model_2d_export"])
def test_plugin_id_accepts_stable_ids(value: str) -> None: ...

@pytest.mark.parametrize("value", ["NC_TO_SHP", "nc-to-shp", "中文", "ab"])
def test_plugin_id_rejects_invalid_ids(value: str) -> None: ...

@pytest.mark.parametrize("value", ["/tmp/a", "C:/temp/a", "../a", "input/../secret"])
def test_relative_protocol_path_rejects_escape(value: str) -> None: ...

def test_strict_model_rejects_unknown_fields() -> None: ...

def test_platform_aliases_are_normalized() -> None:
    assert normalize_os("Linux") == "linux"
    assert normalize_arch("x86_64") == "amd64"
    assert normalize_arch("aarch64") == "arm64"
```

- [ ] **Step 2: Run tests and confirm imports fail**

Run: `python -m pytest tests/contracts/test_common.py -v`

Expected: FAIL because `python_hub_contracts.common` does not exist.

- [ ] **Step 3: Implement strict primitives**

`StrictContractModel` uses `ConfigDict(extra="forbid", frozen=True)`. Implement constrained string aliases and a Pydantic `BeforeValidator` for path normalization. A valid protocol path uses `/`, is not empty, is not absolute, contains no `.` or `..` segment, and has no NUL byte or Windows drive prefix.

Platform helpers accept case-insensitive aliases but return only:

```python
Literal["linux"]
Literal["amd64", "arm64"]
```

Unknown OS/architecture raises `ValueError`.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/contracts/test_common.py -v`

Expected: all common-contract tests pass.

- [ ] **Step 5: Export the public primitives and run quality checks**

Run: `python -m ruff check packages/hub-contracts tests/contracts/test_common.py`

Run: `python -m mypy packages/hub-contracts/src`

Expected: both commands exit 0.

- [ ] **Step 6: Commit common types**

```bash
git add packages/hub-contracts tests/contracts/test_common.py
git commit -m "feat(contracts): add strict protocol primitives"
```

## Task 3: Plugin and Build Manifests

**Files:**
- Create: `packages/hub-contracts/src/python_hub_contracts/plugin_manifest.py`
- Create: `packages/hub-contracts/src/python_hub_contracts/build_manifest.py`
- Create: `packages/hub-contracts/src/python_hub_contracts/yaml_io.py`
- Modify: `packages/hub-contracts/src/python_hub_contracts/__init__.py`
- Test: `tests/contracts/test_plugin_manifest.py`
- Test: `tests/contracts/test_build_manifest.py`
- Test: `tests/contracts/test_yaml_io.py`
- Create: `tests/fixtures/valid-plugin.yaml`
- Create: `tests/fixtures/valid-build.json`

**Interfaces:**
- Consumes: Task 2 strict types.
- Produces: `PluginManifest`, `PluginBuildManifest`, `load_plugin_manifest(path: Path) -> PluginManifest`.

- [ ] **Step 1: Write failing manifest tests**

Cover all of these behaviors with explicit fixtures and assertions:

- The design document's `nc_to_shp` YAML validates.
- Duplicate parameter/input/output names are rejected.
- Unsupported parameter types and runtime types are rejected.
- `enum` requires non-empty unique `options`, and its default must be one of them.
- Required fields cannot define contradictory defaults.
- Numeric `min` cannot exceed `max`; file counts and sizes are positive.
- `build.json` accepts only `linux/amd64` and `linux/arm64` after normalization.
- `plugin_id` and `plugin_version` are validated but cross-file equality is checked by a method named `assert_matches_plugin()`.
- `runtime.environment_path` is a safe relative path and SHA256 is 64 lowercase hexadecimal characters.
- YAML aliases are capped by rejecting documents containing anchors/aliases; duplicate mapping keys are rejected by the custom safe loader.

- [ ] **Step 2: Run manifest tests and confirm they fail**

Run: `python -m pytest tests/contracts/test_plugin_manifest.py tests/contracts/test_build_manifest.py tests/contracts/test_yaml_io.py -v`

Expected: FAIL because manifest types and loader are missing.

- [ ] **Step 3: Implement `PluginManifest`**

Create frozen, extra-forbidden models for:

```text
PluginInfo, SdkSpec, PythonSpec, EnvironmentDeclaration, RuntimeSpec,
EntryPointSpec, ParameterSpec, InputSpec, OutputSpec, ExecutionSpec,
EnvironmentVariablesSpec, HealthcheckSpec, PluginManifest
```

Use discriminated or validated literals for V1 type sets. `PluginManifest` validates uniqueness across each named list and exposes `parameter_by_name`, `input_by_name`, and `output_by_name` methods that raise `KeyError` when absent.

- [ ] **Step 4: Implement `PluginBuildManifest`**

Create `TargetPlatform`, `PackagedRuntime`, and `PluginBuildManifest`. `assert_matches_plugin(plugin: PluginManifest) -> None` raises `ValueError` when ID/version, Python version, or SDK major version differs.

- [ ] **Step 5: Implement safe YAML loading**

`load_plugin_manifest()` reads UTF-8 with a 1 MiB default limit, uses `yaml.SafeLoader` subclassing only to reject duplicate mapping keys, rejects anchors/aliases before construction, requires a mapping root, and passes it to `PluginManifest.model_validate()`.

- [ ] **Step 6: Run focused tests and static checks**

Run: `python -m pytest tests/contracts/test_plugin_manifest.py tests/contracts/test_build_manifest.py tests/contracts/test_yaml_io.py -v`

Run: `python -m ruff check packages/hub-contracts tests/contracts`

Run: `python -m mypy packages/hub-contracts/src`

Expected: all commands exit 0.

- [ ] **Step 7: Commit manifest contracts**

```bash
git add packages/hub-contracts tests/contracts tests/fixtures
git commit -m "feat(contracts): define plugin and build manifests"
```

## Task 4: Job Runtime and Runner Event Contracts

**Files:**
- Create: `packages/hub-contracts/src/python_hub_contracts/job_protocol.py`
- Create: `packages/hub-contracts/src/python_hub_contracts/runner_events.py`
- Modify: `packages/hub-contracts/src/python_hub_contracts/__init__.py`
- Test: `tests/contracts/test_job_protocol.py`
- Test: `tests/contracts/test_runner_events.py`

**Interfaces:**
- Consumes: Task 2 relative paths, IDs and strict model base.
- Produces: `JobRuntimeSpec`, `JobResult`, `ProgressEvent`, `LogEvent`, `parse_runner_line(line: str) -> RunnerEvent | None`.

- [ ] **Step 1: Write failing `job.json` and `result.json` tests**

Tests construct the exact success and failure examples from the design and verify:

- protocol version is `1.0`;
- timestamps are timezone-aware;
- input SHA256, positive sizes, relative paths and timeout are validated;
- `SUCCESS` requires `error is None`;
- `FAILED` requires an error and permits no unregistered output outside the modeled list;
- `CANCELLED` permits an optional stable cancellation error;
- progress/state values use enums serialized as uppercase strings;
- JSON round-trip with `model_dump_json()`/`model_validate_json()` is lossless.

- [ ] **Step 2: Write failing Runner Event tests**

```python
def test_parse_progress_event() -> None:
    event = parse_runner_line(
        '@@HUB@@{"protocol_version":"1.0","type":"progress",'
        '"percent":50,"message":"处理中"}'
    )
    assert isinstance(event, ProgressEvent)
    assert event.percent == 50

def test_plain_stdout_is_not_an_event() -> None:
    assert parse_runner_line("ordinary plugin output") is None
```

Also verify percent bounds, supported log levels, malformed protocol JSON raising `RunnerEventParseError`, and unknown event types raising the same stable exception.

- [ ] **Step 3: Run focused tests and confirm failure**

Run: `python -m pytest tests/contracts/test_job_protocol.py tests/contracts/test_runner_events.py -v`

Expected: FAIL because job and event modules are absent.

- [ ] **Step 4: Implement job protocol models**

Define enums `JobStatus`, `FileRole`, and the exact nested types used by the design. Enforce cross-field result invariants with `model_validator(mode="after")`. Model input values as `RuntimeInputFile | list[RuntimeInputFile]` and require non-empty lists.

- [ ] **Step 5: Implement event parser**

Use `RUNNER_EVENT_PREFIX = "@@HUB@@"` and a Pydantic discriminated union on `type`. Return `None` for ordinary stdout; raise `RunnerEventParseError(code="RUNNER_EVENT_INVALID", message=...)` only for prefixed but invalid data.

- [ ] **Step 6: Run protocol tests and static checks**

Run: `python -m pytest tests/contracts/test_job_protocol.py tests/contracts/test_runner_events.py -v`

Run: `python -m ruff check packages/hub-contracts tests/contracts`

Run: `python -m mypy packages/hub-contracts/src`

Expected: all commands exit 0.

- [ ] **Step 7: Commit runtime contracts**

```bash
git add packages/hub-contracts tests/contracts
git commit -m "feat(contracts): add job and runner event protocols"
```

## Task 5: Plugin SDK Result and Error Types

**Files:**
- Create: `packages/hub-sdk/src/python_hub_sdk/result.py`
- Create: `packages/hub-sdk/src/python_hub_sdk/errors.py`
- Modify: `packages/hub-sdk/src/python_hub_sdk/__init__.py`
- Test: `tests/sdk/test_result.py`
- Test: `tests/sdk/test_errors.py`

**Interfaces:**
- Consumes: standard library dataclasses/path types only.
- Produces: `InputFile`, `OutputFile`, `PluginResult`, `PluginError`, `PluginValidationError`, `PluginExecutionError`, `PluginCancelledError`.

- [ ] **Step 1: Write failing SDK value-object tests**

Tests prove that `InputFile` is immutable, `OutputFile.path` rejects absolute/traversal paths, `PluginResult` does not share mutable defaults, and result data must be JSON-serializable.

- [ ] **Step 2: Write failing exception tests**

```python
def test_plugin_validation_error_has_stable_fields() -> None:
    error = PluginValidationError(code="INVALID_TIME_RANGE", message="范围非法")
    assert str(error) == "范围非法"
    assert error.code == "INVALID_TIME_RANGE"
    assert error.error_type == "PluginValidationError"
```

Verify codes match `^[A-Z][A-Z0-9_]{2,63}$` and `PluginCancelledError()` defaults to `PLUGIN_CANCELLED` / `任务已取消`.

- [ ] **Step 3: Run SDK tests and confirm failure**

Run: `python -m pytest tests/sdk/test_result.py tests/sdk/test_errors.py -v`

Expected: FAIL because SDK modules are missing.

- [ ] **Step 4: Implement immutable SDK types**

Use frozen dataclasses with slots. Validate in `__post_init__`; reuse no Hub-internal code. Check JSON serializability via `json.dumps(data, ensure_ascii=False)`. Expose only public SDK names from `python_hub_sdk.__init__`.

- [ ] **Step 5: Implement exception hierarchy**

`PluginError` stores `code`, `message`, and optional JSON-safe `details`. Derived exceptions change only semantic type/defaults. No exception stores Hub database objects or HTTP status codes.

- [ ] **Step 6: Run SDK tests and quality checks**

Run: `python -m pytest tests/sdk/test_result.py tests/sdk/test_errors.py -v`

Run: `python -m ruff check packages/hub-sdk tests/sdk`

Run: `python -m mypy packages/hub-sdk/src`

Expected: all commands exit 0.

- [ ] **Step 7: Commit SDK value types**

```bash
git add packages/hub-sdk tests/sdk
git commit -m "feat(sdk): add plugin result and error contracts"
```

## Task 6: Plugin Context, Events, and Path Safety

**Files:**
- Create: `packages/hub-sdk/src/python_hub_sdk/context.py`
- Modify: `packages/hub-sdk/src/python_hub_sdk/__init__.py`
- Test: `tests/sdk/test_context.py`

**Interfaces:**
- Consumes: `PluginCancelledError` and callable event/cancellation adapters.
- Produces: `PluginContext`, `PluginLogger` protocol, `EventSink` protocol, and safe `output_file()`/`work_file()` helpers.

- [ ] **Step 1: Write failing Context tests**

Tests cover:

- `progress(0)` and `progress(100, "done")` emit exact dictionaries.
- percent below 0 or above 100 raises `ValueError`.
- `is_cancelled()` delegates to an injected zero-argument callable.
- `check_cancelled()` raises `PluginCancelledError` only when requested.
- `output_file("result.zip")` resolves under output root.
- absolute paths, `..`, and a symlink that resolves outside the root are rejected.
- returned parent directories are created only when `create_parent=True`.

- [ ] **Step 2: Run Context tests and confirm failure**

Run: `python -m pytest tests/sdk/test_context.py -v`

Expected: FAIL because `PluginContext` is missing.

- [ ] **Step 3: Implement adapter-based Context**

Constructor signature:

```python
PluginContext(
    *,
    job_id: str,
    plugin_id: str,
    plugin_version: str,
    input_dir: Path,
    work_dir: Path,
    output_dir: Path,
    logger: PluginLogger,
    event_sink: EventSink,
    cancellation_probe: Callable[[], bool],
) -> None
```

`progress()` calls `event_sink.emit_progress(percent, message)`. Path helpers resolve the root and candidate, use `Path.relative_to()` to prove containment, reject existing symlink escape, and never accept absolute input.

- [ ] **Step 4: Run Context tests and quality checks**

Run: `python -m pytest tests/sdk/test_context.py -v`

Run: `python -m ruff check packages/hub-sdk tests/sdk`

Run: `python -m mypy packages/hub-sdk/src`

Expected: all commands exit 0.

- [ ] **Step 5: Commit Context**

```bash
git add packages/hub-sdk tests/sdk
git commit -m "feat(sdk): add plugin execution context"
```

## Task 7: Cross-Package Contract Verification and Phase Acceptance

**Files:**
- Modify: `README.md`
- Create: `tests/test_contract_examples.py`

**Interfaces:**
- Consumes: all public contracts and SDK types from Tasks 1–6.
- Produces: a documented, tested protocol-foundation release candidate for later Runner and Server phases.

- [ ] **Step 1: Write an end-to-end contract test**

The test loads `valid-plugin.yaml`, validates `valid-build.json`, calls `assert_matches_plugin()`, constructs a `JobRuntimeSpec`, round-trips it through JSON, constructs a `PluginResult`, maps it into a successful `JobResult`, and parses one progress event. Assert IDs, versions, paths and status remain unchanged across the chain.

- [ ] **Step 2: Run the end-to-end test and confirm the first missing integration behavior**

Run: `python -m pytest tests/test_contract_examples.py -v`

Expected: FAIL until all needed public exports and conversion helpers are present.

- [ ] **Step 3: Add only the missing public exports/conversion helpers**

Keep conversion explicit: add `OutputFile.to_protocol_dict()` only if the test requires it. Do not introduce Runner, database, HTTP, archive installation, or subprocess code in this phase.

- [ ] **Step 4: Document package boundaries and commands**

README must show:

```text
python -m pytest --cov=python_hub_contracts --cov=python_hub_sdk --cov-report=term-missing
python -m ruff check .
python -m mypy packages
```

It must include a minimal `plugin.yaml`, SDK `run()` signature, and an explicit statement that platform data belongs in `build.json`.

- [ ] **Step 5: Run the complete verification suite**

Run: `python -m pytest --cov=python_hub_contracts --cov=python_hub_sdk --cov-report=term-missing`

Expected: all tests pass and combined package coverage is at least 90%.

Run: `python -m ruff check .`

Expected: exit 0 with no findings.

Run: `python -m mypy packages`

Expected: exit 0 with no type errors.

- [ ] **Step 6: Verify design invariants by search**

Run: `rg -n "fastapi|sqlalchemy|redis|subprocess" packages/hub-sdk/src`

Expected: no matches.

Run: `rg -n "(^|[^a-z])targets:" tests/fixtures/valid-plugin.yaml packages/hub-contracts/src`

Expected: no matches; platform target is absent from the source manifest.

- [ ] **Step 7: Commit phase acceptance**

```bash
git add README.md tests/test_contract_examples.py packages
git commit -m "test: verify protocol foundation end to end"
```

## Phase Completion Gate

The phase is complete only when:

- all tests pass with at least 90% combined coverage;
- Ruff and strict mypy pass;
- both packages build as wheels;
- the design examples validate without modification;
- invalid IDs, versions, paths, manifests, result invariants and events have negative tests;
- `plugin.yaml` contains no target architecture;
- SDK contains no server/framework dependency;
- no HTTP, ORM, subprocess executor or environment installer behavior has leaked into this phase.

After this gate, create separate implementation plans in this order:

1. persistence and filesystem foundation;
2. Runner and ProcessExecutor;
3. Plugin/Environment secure installation;
4. REST API V1;
5. `nc_to_shp` reference plugin;
6. multi-architecture Docker and offline release.
