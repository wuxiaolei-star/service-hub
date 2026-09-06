# Dual Runtime Plugin V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Docker Compose Service Hub that registers and runs the `nc_to_shp` plugin through independently installable `conda-pack` and Docker Builds, then verifies their NC conversion outputs are semantically equal.

**Architecture:** `hub-server` owns the public API, SQLite metadata and safe files; two internal workers claim work through a token-protected internal API. `hub-conda-runner` safely unpacks and executes Conda Builds without Docker Socket access, while `hub-docker-runner` imports and starts Docker Builds using the Docker Socket but never executes plugin code itself. Both consume the same strict manifests, job protocol, file model and result protocol.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic 2.x, Docker SDK for Python, zstandard, Conda Pack, Docker Compose, GDAL/OGR, h5py, NumPy, SciPy, pytest, httpx.

**Spec:** `docs/superpowers/specs/2026-09-06-dual-runtime-plugin-v1-design.md`

## Global Constraints

- Target first release platform is Linux AMD64; ARM64 uses separate Build packages made from identical source.
- Build packages use `runtime.type` exactly `conda-pack` or `docker`; automatic fallback is forbidden.
- A Build is unique on `(plugin_version_id, target_os, target_arch, runtime_type)` and never overwritten.
- Hub never resolves or installs plugin business dependencies. Conda and Docker artifacts are prebuilt by the publisher.
- External API stays under `/api/v1`; all failures use the existing `ErrorResponse` envelope.
- External Job requests may choose `runtime_type`; omitted means `docker`.
- Canonical Job states are `PENDING`, `PREPARING`, `RUNNING`, `SUCCESS`, `FAILED`, `CANCELLED`, `TIMED_OUT`; cancellation intent is `cancel_requested: bool`.
- Only `hub-docker-runner` mounts the Docker Socket. Conda runner and every plugin container must not receive it.
- Docker plugin containers run as non-root, no network, no privileged mode, and only receive read-only input/job mounts plus writable output.
- Package extraction rejects path traversal, absolute paths, duplicate members, undeclared members, oversized data and root-escaping links.
- The real NC fixture is supplied outside Git by `HUB_NC_SAMPLE_PATH`; it is never copied into the repository.
- Every task starts with a failing test and ends with focused tests, Ruff, strict mypy where applicable, and a commit.

---

## File structure and task dependencies

```text
packages/
├── hub-contracts/                         # Tasks 1–2: dual Build/job contracts
├── hub-sdk/                               # Task 5: runner event/context extensions only if required
├── hub-runner/                            # Task 5: plugin entrypoint and result writer
├── hub-server/
│   └── src/hub_server/
│       ├── models.py                      # Task 2
│       ├── services/{plugins,jobs,...}.py # Tasks 3–4, 6–8
│       ├── routers/{plugins,jobs,...}.py  # Tasks 4 and 8
│       └── routers/internal_runner.py     # Tasks 4, 6 and 7
└── nc-to-shp-plugin/                      # Task 9
    ├── src/nc_to_shp_plugin/
    ├── plugin.yaml
    ├── conda/environment.yml
    ├── docker/Dockerfile
    └── scripts/package_build.py
alembic/versions/0002_plugins_jobs.py      # Task 2
deploy/runner/                             # Tasks 6–7: runner images/entrypoints
tests/contracts/                           # Task 1
tests/server/                              # Tasks 2–4 and 8
tests/runner/                              # Tasks 5–7
tests/plugins/                             # Task 9
tests/integration/                         # Task 10
```

Task 1 defines types used by every later task. Task 2 creates durable metadata. Tasks 3–4 make Build installation and Job claiming available. Task 5 provides the common in-environment plugin runner. Tasks 6 and 7 implement each isolated executor. Task 8 adds public lifecycle APIs. Task 9 creates the real plugin and two packages. Task 10 runs the required Linux AMD64 acceptance suite.

## Task 1: Reconcile strict protocol contracts for dual runtime

**Files:**

- Modify: `packages/hub-contracts/src/python_hub_contracts/build_manifest.py`
- Modify: `packages/hub-contracts/src/python_hub_contracts/plugin_manifest.py`
- Modify: `packages/hub-contracts/src/python_hub_contracts/job_protocol.py`
- Modify: `packages/hub-contracts/src/python_hub_contracts/__init__.py`
- Modify: `tests/contracts/test_build_manifest.py`
- Modify: `tests/contracts/test_job_protocol.py`
- Modify: `tests/fixtures/valid-build.json`
- Modify: `tests/fixtures/valid-plugin.yaml`

**Interfaces:**

- Produces `CondaPackRuntime`, `DockerRuntime`, `RuntimeBuild = Annotated[..., Field(discriminator="type")]`, and `PluginBuildManifest.runtime: RuntimeBuild`.
- Produces `RuntimeType = Literal["conda-pack", "docker"]` for server and runner imports.
- Produces terminal `JobStatus.TIMED_OUT`; `JobResult` accepts it only with a non-null `JobError` whose code is `JOB_TIMED_OUT`.
- Changes source-level `RuntimeSpec` to platform-neutral `type="process"` and `python`; removes the Conda-only source `environment` declaration.

- [ ] **Step 1: Write failing discriminated-runtime and timeout tests**

```python
def test_valid_docker_build_manifest_matches_plugin(plugin: PluginManifest) -> None:
    data = {
        "schema_version": "1.0", "build_id": "build_docker_1",
        "plugin_id": "nc_to_shp", "plugin_version": "1.0.0",
        "target": {"os": "linux", "arch": "amd64"}, "sdk_version": "1.0.0",
        "source_sha256": "a" * 64, "built_at": "2026-09-06T08:00:00+00:00",
        "runtime": {"type": "docker", "archive": "image.tar.zst",
                    "image": "nc_to_shp:1.0.0-linux-amd64", "digest": "b" * 64},
    }
    assert PluginBuildManifest.model_validate(data).runtime.type == "docker"


def test_timed_out_result_requires_timeout_error() -> None:
    result = valid_result_data(status="TIMED_OUT", error={
        "type": "RunnerTimeout", "code": "JOB_TIMED_OUT", "message": "任务超过时限"
    })
    assert JobResult.model_validate(result).status is JobStatus.TIMED_OUT
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `python -m pytest tests/contracts/test_build_manifest.py tests/contracts/test_job_protocol.py -v`

Expected: FAIL because Docker runtime and `TIMED_OUT` are not accepted.

- [ ] **Step 3: Implement the minimal strict union and state rules**

```python
class CondaPackRuntime(StrictContractModel):
    type: Literal["conda-pack"]
    archive: RelativeProtocolPath
    fingerprint: Sha256


class DockerRuntime(StrictContractModel):
    type: Literal["docker"]
    archive: RelativeProtocolPath
    image: str = Field(min_length=1, max_length=255)
    digest: Sha256


RuntimeBuild = Annotated[CondaPackRuntime | DockerRuntime, Field(discriminator="type")]
```

Replace `PackagedRuntime` with `RuntimeBuild`; keep `python_version` at top-level of `PluginBuildManifest` so both Build types validate it against `plugin.runtime.python.version`. Add `TIMED_OUT` to `JobStatus`, allow it in `JobResult.status`, and require `error.code == "JOB_TIMED_OUT"` when timed out. Update fixture manifests so `plugin.yaml` has no environment dependency declaration.

- [ ] **Step 4: Run contract verification**

Run:

```text
python -m pytest tests/contracts tests/test_contract_examples.py -v
python -m ruff check packages/hub-contracts tests/contracts
python -m mypy packages/hub-contracts/src
```

Expected: all pass.

- [ ] **Step 5: Commit protocol changes**

```bash
git add packages/hub-contracts tests/contracts tests/fixtures tests/test_contract_examples.py
git commit -m "feat(contracts): support conda and docker builds"
```

## Task 2: Add immutable plugin, environment and Job persistence

**Files:**

- Modify: `packages/hub-server/src/hub_server/models.py`
- Create: `packages/hub-server/src/hub_server/repositories.py`
- Create: `alembic/versions/0002_plugins_jobs.py`
- Modify: `tests/server/conftest.py`
- Create: `tests/server/test_plugin_job_models.py`

**Interfaces:**

- Produces SQLAlchemy `Plugin`, `PluginVersion`, `PluginBuild`, `Environment`, `Job`, `JobFile`, `RunnerOperation` models.
- Produces repository methods `create_build_installation(...)`, `claim_operation(runtime_type: RuntimeType)`, `claim_job(runtime_type: RuntimeType)`, and `transition_job(...)`.
- Consumes `RuntimeType`, `JobStatus` from Task 1 and existing `FileRecord`.

- [ ] **Step 1: Write failing model and atomic-claim tests**

```python
def test_build_identity_allows_two_runtime_types_but_not_duplicates(session: Session) -> None:
    conda = create_build(session, runtime_type="conda-pack")
    docker = create_build(session, runtime_type="docker")
    session.add_all([conda, docker])
    session.commit()
    session.add(create_build(session, runtime_type="docker"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_claim_job_only_claims_matching_pending_runtime(session: Session) -> None:
    conda_job = create_job(session, runtime_type="conda-pack", status="PENDING")
    create_job(session, runtime_type="docker", status="PENDING")
    session.commit()
    claimed = HubRepository(session).claim_job("conda-pack")
    assert claimed is not None and claimed.job_key == conda_job.job_key
    assert claimed.status == "PREPARING"
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `python -m pytest tests/server/test_plugin_job_models.py -v`

Expected: FAIL because models and migration do not exist.

- [ ] **Step 3: Implement models, constraints and migration**

Use unguessable public identifiers made of the fixed prefixes `plugin_build_`, `job_`, or `operation_` followed by `uuid4().hex`. Persist package/source hashes, target platform, runtime type, environment path or image digest, statuses, safe error summary, immutable JSON snapshots, timestamps, timeout, cancel flag and exit code. Add foreign keys and indexes for `Job.status`, `PluginBuild.runtime_type` and `RunnerOperation.status`.

Implement claim with one transaction: select the oldest matching pending row, issue `UPDATE ... WHERE status='PENDING'`, require exactly one updated row, then refresh it as `PREPARING`. Never use a read-only selection as the claim itself.

- [ ] **Step 4: Run persistence verification**

Run:

```text
python -m pytest tests/server/test_database.py tests/server/test_plugin_job_models.py -v
python -m ruff check packages/hub-server alembic tests/server
python -m mypy packages/hub-server/src
```

Expected: all pass and a fresh SQLite database upgrades through revision `0002_plugins_jobs`.

- [ ] **Step 5: Commit persistence layer**

```bash
git add packages/hub-server alembic tests/server
git commit -m "feat(server): persist plugin builds and jobs"
```

## Task 3: Implement safe package and Job workspace storage

**Files:**

- Create: `packages/hub-server/src/hub_server/services/archives.py`
- Create: `packages/hub-server/src/hub_server/services/workspaces.py`
- Modify: `packages/hub-server/src/hub_server/storage.py`
- Create: `tests/server/test_archives.py`
- Create: `tests/server/test_job_workspaces.py`

**Interfaces:**

- Produces `VerifiedPluginPackage(manifest: PluginManifest, build: PluginBuildManifest, source_dir: Path, archive: Path)`.
- Produces `PluginArchiveService.verify_and_install(upload: BinaryIO, package_sha256: str) -> VerifiedPluginPackage`.
- Produces `JobWorkspaceService.prepare(job: Job, input_files: list[FileRecord]) -> Path` and `register_output(...) -> FileRecord`.
- Consumes Task 1 manifest types and Task 2 records.

- [ ] **Step 1: Write failing extraction and workspace boundary tests**

```python
def test_package_rejects_member_not_listed_in_checksums(tmp_path: Path) -> None:
    package = make_pypkg(tmp_path, members={"plugin.yaml": b"...", "extra.py": b"x"})
    with pytest.raises(HubError, match="PLUGIN_PACKAGE_CHECKSUM_MISMATCH"):
        PluginArchiveService(tmp_path / "data").verify_and_install(package)


def test_workspace_copies_input_and_rejects_output_escape(tmp_path: Path) -> None:
    workspace = JobWorkspaceService(LocalStorage(tmp_path)).prepare(job, [input_file])
    assert (workspace / "input" / input_file.logical_name).is_file()
    with pytest.raises(ValueError):
        JobWorkspaceService(LocalStorage(tmp_path)).resolve_output(workspace, "../secret.zip")
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `python -m pytest tests/server/test_archives.py tests/server/test_job_workspaces.py -v`

Expected: FAIL because archive and workspace services do not exist.

- [ ] **Step 3: Implement bounded verification and workspace creation**

Read `.pypkg` as zstd-compressed tar without extracting untrusted entries through `extractall`. Validate normalized POSIX names, member count and declared sizes before streaming each regular file to a temporary directory; allow a symlink only after resolving its target under the temporary root. Require exactly `plugin.yaml`, `build.json`, `checksums.json`, `plugin/`, and the runtime-specific archive path. Validate checksums before atomically moving to the `plugins` child directory named by the persisted Build ID.

Create a Job directory under `jobs` named by its persisted Job key, containing `input`, `work`, `output`, and `logs`. Copy or hard-link only available input payloads into `input/`, then serialize a Task 1 `JobRuntimeSpec` as `job.json`. Resolve final outputs only below `output/`; compute SHA256 before inserting output `FileRecord`.

- [ ] **Step 4: Run service verification**

Run:

```text
python -m pytest tests/server/test_archives.py tests/server/test_job_workspaces.py tests/server/test_storage.py -v
python -m ruff check packages/hub-server tests/server
python -m mypy packages/hub-server/src
```

Expected: all pass.

- [ ] **Step 5: Commit storage services**

```bash
git add packages/hub-server tests/server
git commit -m "feat(server): verify plugin packages and job workspaces"
```

## Task 4: Add Plugin Build installation and internal Runner claim API

**Files:**

- Create: `packages/hub-server/src/hub_server/services/plugins.py`
- Create: `packages/hub-server/src/hub_server/services/runner_operations.py`
- Create: `packages/hub-server/src/hub_server/routers/internal_runner.py`
- Modify: `packages/hub-server/src/hub_server/settings.py`
- Modify: `packages/hub-server/src/hub_server/main.py`
- Modify: `packages/hub-server/src/hub_server/schemas.py`
- Modify: `config/hub.yaml.example`
- Create: `tests/server/test_internal_runner_api.py`

**Interfaces:**

- Produces internal endpoints `POST /internal/v1/operations/claim`, `POST /internal/v1/operations/{id}/complete`, `POST /internal/v1/jobs/claim`, `POST /internal/v1/jobs/{id}/events`, and `POST /internal/v1/jobs/{id}/complete`.
- Each endpoint requires header `X-Hub-Runner-Token` equal to `settings.runner.shared_token` and body runtime type.
- Produces `PluginService.stage_installation(...) -> PluginBuild` and `RunnerOperationService.complete(...)`.

- [ ] **Step 1: Write failing internal-token and claim tests**

```python
def test_internal_claim_rejects_missing_or_wrong_token(client: TestClient) -> None:
    assert client.post("/internal/v1/jobs/claim", json={"runtime_type": "docker"}).status_code == 403
    response = client.post(
        "/internal/v1/jobs/claim", headers={"X-Hub-Runner-Token": "wrong"},
        json={"runtime_type": "docker"},
    )
    assert response.json()["error"]["code"] == "RUNNER_AUTH_FAILED"


def test_matching_runner_can_claim_only_its_runtime(client: TestClient) -> None:
    seed_pending_jobs(client.app, ["conda-pack", "docker"])
    response = runner_post(client, "/internal/v1/jobs/claim", {"runtime_type": "docker"})
    assert response.json()["runtime_type"] == "docker"
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `python -m pytest tests/server/test_internal_runner_api.py -v`

Expected: FAIL because internal routes and token settings are absent.

- [ ] **Step 3: Implement the internal API and staged installation service**

Add `RunnerSettings(shared_token: SecretStr, poll_interval_seconds: int = Field(gt=0))`; reject token leakage from errors and system info. Keep internal routes unmounted from host ports and return no filesystem paths. `stage_installation` uses Task 3 verification, persists Plugin/Version/Build/Environment with `INSTALLING`, and creates one matching `RunnerOperation(kind="INSTALL")`. Completion changes Build and Environment atomically to READY or FAILED. Job claim returns serialized `job.json`, workspace-relative paths, Build runtime metadata and no unnecessary database fields.

- [ ] **Step 4: Run API verification**

Run:

```text
python -m pytest tests/server/test_internal_runner_api.py tests/server/test_asgi_config.py -v
python -m ruff check packages/hub-server tests/server
python -m mypy packages/hub-server/src
```

Expected: all pass.

- [ ] **Step 5: Commit runner coordination API**

```bash
git add packages/hub-server config tests/server
git commit -m "feat(server): coordinate internal runtime runners"
```

## Task 5: Build the common in-environment `hub-runner` CLI

**Files:**

- Create: `packages/hub-runner/pyproject.toml`
- Create: `packages/hub-runner/src/hub_runner/__init__.py`
- Create: `packages/hub-runner/src/hub_runner/__main__.py`
- Create: `packages/hub-runner/src/hub_runner/execution.py`
- Create: `packages/hub-runner/src/hub_runner/result_writer.py`
- Modify: root `pyproject.toml`
- Create: `tests/runner/test_execution.py`
- Create: `tests/runner/test_result_writer.py`

**Interfaces:**

- Produces CLI: `python -m hub_runner --job PATH --manifest PATH --plugin-root PATH --result PATH`.
- Consumes `JobRuntimeSpec`, `PluginManifest`, SDK `PluginContext` and plugin entrypoint.
- Produces atomic validated `JobResult` and stdout Runner events; returns zero for `SUCCESS`, nonzero for `FAILED`, `CANCELLED`, and `TIMED_OUT`.

- [ ] **Step 1: Write failing plugin-loading and atomic-result tests**

```python
def test_runner_loads_entrypoint_and_writes_success_result(tmp_path: Path) -> None:
    exit_code = run_job(job_path, manifest_path, plugin_root, result_path)
    result = JobResult.model_validate_json(result_path.read_text("utf-8"))
    assert exit_code == 0
    assert result.status is JobStatus.SUCCESS


def test_runner_turns_plugin_validation_error_into_failed_result(tmp_path: Path) -> None:
    exit_code = run_job(job_path, failing_manifest_path, plugin_root, result_path)
    result = JobResult.model_validate_json(result_path.read_text("utf-8"))
    assert exit_code != 0
    assert result.error is not None and result.error.code == "NC_GROUP_NOT_FOUND"
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `python -m pytest tests/runner/test_execution.py tests/runner/test_result_writer.py -v`

Expected: FAIL because `hub_runner` is not importable.

- [ ] **Step 3: Implement runner execution without server dependencies**

Load only the manifest-declared module/function from `plugin_root`; construct `InputFile` values from safe Job-relative input paths. Convert known SDK errors to stable `JobError`; capture unexpected tracebacks only in `logs/runner.log`. Write the result to `result_path.with_suffix(".tmp")`, flush and fsync, validate with `JobResult`, then `os.replace`. Emit `@@HUB@@` JSON events for start, progress/log messages, and terminal completion. Never import FastAPI, SQLAlchemy or Docker SDK in this package.

- [ ] **Step 4: Run runner verification**

Run:

```text
python -m pytest tests/runner tests/sdk tests/contracts/test_job_protocol.py -v
python -m ruff check packages/hub-runner tests/runner
python -m mypy packages/hub-runner/src
```

Expected: all pass.

- [ ] **Step 5: Commit common runner**

```bash
git add pyproject.toml packages/hub-runner tests/runner
git commit -m "feat(runner): execute plugin jobs and write results"
```

## Task 6: Implement Conda package installation and isolated Conda runner

**Files:**

- Create: `packages/hub-runner/src/hub_runner/conda_executor.py`
- Create: `deploy/runner/Dockerfile.conda-runner`
- Create: `deploy/runner/conda_runner_service.py`
- Create: `tests/runner/test_conda_executor.py`
- Modify: `packages/hub-server/src/hub_server/services/runner_operations.py`
- Modify: `compose.yaml`

**Interfaces:**

- Produces `CondaExecutor.install(build: RunnerBuild) -> InstallResult` and `CondaExecutor.execute(job: RunnerJob) -> RunnerCompletion`.
- Conda executor consumes only `conda-pack` operations/jobs and executes `environment_path / "bin" / "python"` with module argument `hub_runner`.
- Compose service `hub-conda-runner` shares `/data`, has no published ports and no Docker Socket mount.

- [ ] **Step 1: Write failing Conda command and rejection tests**

```python
def test_conda_executor_runs_conda_unpack_then_import_healthcheck(tmp_path: Path) -> None:
    executor = CondaExecutor(data_root=tmp_path, command_runner=fake_runner)
    executor.install(conda_build)
    assert fake_runner.calls == [
        [str(tmp_path / "environments" / conda_build.id / "bin" / "conda-unpack")],
        [str(tmp_path / "environments" / conda_build.id / "bin" / "python"), "-c", "import h5py, scipy; from osgeo import ogr"],
    ]


def test_conda_executor_rejects_docker_build() -> None:
    with pytest.raises(ValueError, match="conda-pack"):
        CondaExecutor(data_root=Path("/data")).install(docker_build)
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `python -m pytest tests/runner/test_conda_executor.py -v`

Expected: FAIL because `CondaExecutor` is absent.

- [ ] **Step 3: Implement Conda extraction, health check and process control**

Use Task 3 verified archive only. Stream-decompress it into a temporary child of `environments` named from the Build ID plus a random UUID, validate all entries, atomically rename it to the Build-ID directory, run its `bin/conda-unpack`, then import `h5py`, `scipy`, and `osgeo.ogr`. Execute each job in its own process group with environment variables limited to job/work/output roots; enforce timeout, observe cancellation, and forward parsed Runner events to Task 4 internal API. On startup, mark orphan PREPARING/RUNNING Conda jobs FAILED with `HUB_RESTARTED` before polling.

- [ ] **Step 4: Run Conda runner verification**

Run:

```text
python -m pytest tests/runner/test_conda_executor.py tests/runner/test_execution.py -v
python -m ruff check packages/hub-runner deploy/runner tests/runner
python -m mypy packages/hub-runner/src
```

Expected: all pass.

- [ ] **Step 5: Commit Conda Runner**

```bash
git add packages/hub-runner deploy/runner compose.yaml tests/runner packages/hub-server
git commit -m "feat(runner): execute conda-pack plugin builds"
```

## Task 7: Implement Docker Build installation and isolated Docker runner

**Files:**

- Create: `packages/hub-runner/src/hub_runner/docker_executor.py`
- Create: `deploy/runner/Dockerfile.docker-runner`
- Create: `deploy/runner/docker_runner_service.py`
- Create: `tests/runner/test_docker_executor.py`
- Modify: `compose.yaml`
- Modify: `tests/server/test_container_smoke.py`

**Interfaces:**

- Produces `DockerExecutor.install(build: RunnerBuild) -> InstallResult` and `DockerExecutor.execute(job: RunnerJob) -> RunnerCompletion`.
- Docker executor consumes only `docker` operations/jobs, validates image digest after load, and starts one non-privileged container per Job.
- Compose service `hub-docker-runner` has `/var/run/docker.sock:/var/run/docker.sock`; no other service receives this mount.

- [ ] **Step 1: Write failing Docker SDK argument and isolation tests**

```python
def test_docker_executor_runs_digest_pinned_container_with_only_job_mounts() -> None:
    client = FakeDockerClient()
    DockerExecutor(client, data_root=Path("/data")).execute(docker_job)
    assert client.run_kwargs["image"] == docker_job.image_digest
    assert client.run_kwargs["network_mode"] == "none"
    assert client.run_kwargs["user"] == "65532:65532"
    assert set(client.run_kwargs["volumes"].values()) == {"ro", "rw"}
    assert "/var/run/docker.sock" not in client.run_kwargs["volumes"]


def test_docker_executor_rejects_conda_build() -> None:
    with pytest.raises(ValueError, match="docker"):
        DockerExecutor(FakeDockerClient(), Path("/data")).install(conda_build)
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `python -m pytest tests/runner/test_docker_executor.py -v`

Expected: FAIL because `DockerExecutor` is absent.

- [ ] **Step 3: Implement Docker archive loading and job lifecycle**

Use Docker SDK and zstandard to stream the verified archive through `images.load`; inspect the loaded image and require its immutable ID/digest equals Build metadata before READY. Start containers with digest-pinned image, `network_mode="none"`, `read_only=True`, `user="65532:65532"`, `cap_drop=["ALL"]`, CPU/memory limits from the manifest, and only input/job/output bind mounts. Stream stdout/stderr, parse valid `@@HUB@@` events, enforce timeout/cancel with stop then kill, and register the Runner-generated result through the internal API. On worker startup reconcile its interrupted jobs.

- [ ] **Step 4: Run Docker runner verification**

Run:

```text
python -m pytest tests/runner/test_docker_executor.py tests/server/test_container_smoke.py -v
python -m ruff check packages/hub-runner deploy/runner tests/runner tests/server
python -m mypy packages/hub-runner/src
docker compose config
```

Expected: unit/static checks pass; the selected Compose integration test is deferred to Linux AMD64 if no local Linux Docker daemon exists.

- [ ] **Step 5: Commit Docker Runner**

```bash
git add packages/hub-runner deploy/runner compose.yaml tests/runner tests/server
git commit -m "feat(runner): execute docker plugin builds"
```

## Task 8: Expose public Plugin Build and Job lifecycle APIs

**Files:**

- Create: `packages/hub-server/src/hub_server/routers/plugins.py`
- Create: `packages/hub-server/src/hub_server/routers/jobs.py`
- Create: `packages/hub-server/src/hub_server/services/jobs.py`
- Modify: `packages/hub-server/src/hub_server/schemas.py`
- Modify: `packages/hub-server/src/hub_server/main.py`
- Create: `tests/server/test_plugins_api.py`
- Create: `tests/server/test_jobs_api.py`

**Interfaces:**

- Produces public install/list/build-detail/enable/disable endpoints from the spec.
- Produces `POST /api/v1/jobs` with `runtime_type: Literal["conda-pack", "docker"] | None`; resolves omitted to Docker.
- Produces cancellation, cursor-based logs and output File list responses with no absolute paths.

- [ ] **Step 1: Write failing public API tests**

```python
def test_create_job_uses_explicit_enabled_conda_build(client: TestClient) -> None:
    build, source_file = seed_enabled_build(client.app, runtime_type="conda-pack")
    response = client.post("/api/v1/jobs", json={
        "plugin_id": "nc_to_shp", "version": "1.0.0", "runtime_type": "conda-pack",
        "inputs": {"source_nc": source_file.file_key}, "params": valid_nc_params(),
    })
    assert response.status_code == 201
    assert response.json()["runtime_type"] == "conda-pack"


def test_job_without_runtime_type_defaults_to_docker(client: TestClient) -> None:
    seed_enabled_build(client.app, runtime_type="docker")
    response = client.post("/api/v1/jobs", json=valid_job_request_without_runtime())
    assert response.status_code == 201
    assert response.json()["runtime_type"] == "docker"
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `python -m pytest tests/server/test_plugins_api.py tests/server/test_jobs_api.py -v`

Expected: FAIL because public plugin/job routers are absent.

- [ ] **Step 3: Implement stable public APIs and state validation**

Install accepts one `.pypkg` multipart file and returns 202 Build summary. Enable only READY Builds, and disable only when no RUNNING Job references the Build. Job creation resolves exact enabled Build by plugin/version/current platform/runtime, validates manifest parameters and file extension/size, snapshots inputs and resolved params, then persists PENDING Job and workspace. Cancellation returns 202 idempotently, marks `cancel_requested`, and never claims completion itself. Logs accept `cursor` and `limit`; outputs return File summaries only after terminal success.

- [ ] **Step 4: Run API verification**

Run:

```text
python -m pytest tests/server/test_plugins_api.py tests/server/test_jobs_api.py tests/server/test_files_api.py -v
python -m ruff check packages/hub-server tests/server
python -m mypy packages/hub-server/src
```

Expected: all pass.

- [ ] **Step 5: Commit public lifecycle APIs**

```bash
git add packages/hub-server tests/server
git commit -m "feat(server): expose plugin builds and jobs"
```

## Task 9: Package the real NC-to-Shapefile plugin for both runtimes

**Files:**

- Create: `packages/nc-to-shp-plugin/pyproject.toml`
- Create: `packages/nc-to-shp-plugin/plugin.yaml`
- Create: `packages/nc-to-shp-plugin/src/nc_to_shp_plugin/main.py`
- Create: `packages/nc-to-shp-plugin/src/nc_to_shp_plugin/convert.py`
- Create: `packages/nc-to-shp-plugin/conda/environment.yml`
- Create: `packages/nc-to-shp-plugin/docker/Dockerfile`
- Create: `packages/nc-to-shp-plugin/scripts/package_build.py`
- Create: `packages/nc-to-shp-plugin/scripts/compare_shapefile_zip.py`
- Create: `tests/plugins/test_nc_to_shp.py`
- Create: `tests/plugins/test_package_build.py`

**Interfaces:**

- Produces `run(params: dict[str, object], inputs: dict[str, InputFile | list[InputFile]], context: PluginContext) -> PluginResult`.
- Produces `python scripts/package_build.py --runtime conda-pack --arch amd64 --output DIR` and matching Docker invocation.
- Produces a ZIP containing requested depth/stage Shapefile components and `manifest.json`.
- Produces `compare_shapefile_zip.py left.zip right.zip`, exiting zero only for semantic equality.

- [ ] **Step 1: Write failing NC validation, ZIP and package tests**

```python
def test_converter_rejects_unknown_group(sample_nc: Path, plugin_context: PluginContext) -> None:
    with pytest.raises(PluginValidationError, match="NC_GROUP_NOT_FOUND"):
        run({**valid_params(), "group_name": "missing"}, {"source_nc": InputFile.from_path(sample_nc)}, plugin_context)


def test_converter_writes_depth_and_stage_zip(sample_nc: Path, plugin_context: PluginContext) -> None:
    result = run(valid_params(), {"source_nc": InputFile.from_path(sample_nc)}, plugin_context)
    archive = plugin_context.output_file(result.files[0].path)
    assert zip_names(archive) == expected_shapefile_names("depth", "stage", 1, 20)


def test_package_script_writes_runtime_specific_build_manifest(tmp_path: Path) -> None:
    package = build_package(runtime_type="docker", output_dir=tmp_path)
    assert PluginBuildManifest.model_validate(read_member(package, "build.json")).runtime.type == "docker"
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `python -m pytest tests/plugins/test_nc_to_shp.py tests/plugins/test_package_build.py -v`

Expected: FAIL because the plugin package does not exist.

- [ ] **Step 3: Implement portable conversion and reproducible packaging**

Refactor the supplied algorithm without its Windows `PROJ_LIB` assignment. Validate HDF5 Group, `xyzgeo` or `xyz`, `cells`, selected metric dimensions and time range; map cell values to vertices with the sparse relationship matrix; treat 9999.0/NaN as invalid and emit -9999.0 where no valid associations exist. Write PointZ layers using GDAL/OGR, then ZIP all requested components plus a manifest recording inputs, parameters, CRS, feature count and source hash.

Pin Conda packages in `environment.yml`; Dockerfile installs the same application dependencies and the exact `python-hub-runner` wheel. Package script builds the target runtime externally, collects plugin source, calculates all checksums, emits strict Build metadata and zstd-compressed `.pypkg`; it never runs as Hub installation code.

- [ ] **Step 4: Run plugin verification**

Run:

```text
python -m pytest tests/plugins -v
python -m ruff check packages/nc-to-shp-plugin tests/plugins
python -m mypy packages/nc-to-shp-plugin/src
```

Expected: all unit tests pass with a small generated HDF5 fixture; the real supplied NC remains integration-only.

- [ ] **Step 5: Commit NC plugin**

```bash
git add packages/nc-to-shp-plugin tests/plugins
git commit -m "feat(plugin): add dual-runtime nc to shapefile build"
```

## Task 10: Add Linux AMD64 dual-runtime Compose acceptance and operator documentation

**Files:**

- Modify: `compose.yaml`
- Modify: `Dockerfile`
- Modify: `deploy/nginx/python-service-hub.conf.example`
- Modify: `README.md`
- Create: `docs/guides/双运行时插件构建与部署.md`
- Create: `tests/integration/test_dual_runtime_nc_to_shp.py`
- Modify: `tests/server/test_container_smoke.py`

**Interfaces:**

- Produces a Compose stack with `hub`, `hub-conda-runner`, `hub-docker-runner`; only hub publishes loopback port and only Docker runner mounts the Socket.
- Produces integration command `HUB_NC_SAMPLE_PATH=/absolute/sample.nc python -m pytest -m integration tests/integration/test_dual_runtime_nc_to_shp.py -v`.
- Produces operator instructions for building/transporting AMD64 packages, installing each Build, running both Jobs and later creating ARM64 artifacts.

- [ ] **Step 1: Write failing Compose topology and semantic-comparison acceptance test**

```python
@pytest.mark.integration
def test_amd64_conda_and_docker_nc_jobs_are_semantically_equal() -> None:
    sample = Path(os.environ["HUB_NC_SAMPLE_PATH"]).resolve()
    assert sample.is_file()
    conda_build = install_and_enable("nc_to_shp-1.0.0-linux-amd64-conda.pypkg")
    docker_build = install_and_enable("nc_to_shp-1.0.0-linux-amd64-docker.pypkg")
    conda_output = wait_for_success(create_job(sample, runtime_type="conda-pack"))
    docker_output = wait_for_success(create_job(sample, runtime_type="docker"))
    assert_ogr_zip_equivalent(download(conda_output), download(docker_output), expected_features=39464)
```

- [ ] **Step 2: Run topology tests and confirm they fail**

Run: `python -m pytest tests/server/test_container_smoke.py -v`

Expected: FAIL because Compose has no two Runner services or Socket-isolation assertions.

- [ ] **Step 3: Implement Compose, smoke isolation and operator runbook**

Build separate Hub/Conda Runner/Docker Runner images. Publish only `hub` at `127.0.0.1:8000`; mount `/data` into all three and mount `/var/run/docker.sock` only into Docker Runner. Extend disposable smoke Compose generation so it starts all three services with a random project and port, then always removes only that project and temporary data directory.

Document exact AMD64 build, environment prerequisites, package generation, `POST /plugins/install`, Build polling/enabling, Conda/Docker Job commands, semantic comparator command, backup paths and ARM64 rebuild procedure. State that Nginx must restrict external callers because API-key authentication remains out of scope.

- [ ] **Step 4: Run full verification and Linux release gate**

Run locally:

```text
python -m pytest
python -m ruff check .
python -m mypy packages
docker compose config
```

Run on the Linux AMD64 test server:

```text
docker compose up -d --build
HUB_NC_SAMPLE_PATH=/absolute/path/20260828213102_生成hdf5结果详情信息.nc python -m pytest -m integration tests/integration/test_dual_runtime_nc_to_shp.py -v
```

Expected: both Build installations and both Jobs succeed; OGR semantic comparison passes; no service except Hub has a host port; only Docker Runner has Docker Socket access.

- [ ] **Step 5: Commit deployment acceptance**

```bash
git add Dockerfile compose.yaml deploy README.md docs tests
git commit -m "feat(deploy): verify dual runtime plugin execution"
```

## Final release checklist

- [ ] Contract tests prove both strict Build variants, safe paths and `TIMED_OUT` result semantics.
- [ ] SQLite migration proves two runtime Build rows coexist for the same plugin/version/AMD64 target.
- [ ] Conda and Docker workers reject work for the other runtime type.
- [ ] Only Docker Runner mounts Docker Socket; neither Hub nor Conda Runner does.
- [ ] Hub validates package hashes and never installs plugin dependencies.
- [ ] The supplied NC input succeeds with both runtime types and OGR semantic comparison passes.
- [ ] Linux AMD64 Compose release gate passes; operator guide documents later ARM64 rebuild and offline transfer.
