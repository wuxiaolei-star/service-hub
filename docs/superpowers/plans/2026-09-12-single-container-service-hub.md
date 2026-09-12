# Python Service Hub 单容器双运行时实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 将现有三个长期容器整合为一个可离线交付的 Service Hub 容器，同时保留 conda-pack 和 Docker 一次性 Job、现有协议、数据兼容性与 NC→SHP 双运行时能力。

**Architecture:** 单一镜像使用 tini 和 supervisord 管理 Hub API、conda Runner、Docker Runner 三个降权进程；一个宿主机目录持久化全部数据，只有 Docker Runner 获得 Docker Socket 组权限。服务端 API 和 .pypkg 契约不变，另行提供宿主机 hubctl、通用发布者构建器和离线安装脚本。

**Tech Stack:** Python 3.12、FastAPI、Pydantic 2、SQLAlchemy、SQLite、Docker SDK 7、zstandard、tini、supervisord、Docker Compose、pytest、ruff、mypy。

**Spec:** docs/superpowers/specs/2026-09-12-single-container-service-hub-design.md

## Global Constraints

- 目标运行和验收平台是原生 Linux AMD64。
- 最终只构建 python-service-hub:1.0.0-linux-amd64 一个 Hub 运行镜像。
- compose.yaml 只能声明一个长期运行服务 service-hub。
- 必须同时保留 conda-pack 和 Docker 两种一次性 Job。
- 插件依赖由发布者在目标平台预构建；Hub 不执行在线依赖安装或编译。
- plugin.yaml、build.json、.pypkg、公共 REST API、数据库模型、Job Runtime Protocol 和 Runner Event Protocol 保持兼容。
- 宿主数据目录必须是绝对路径，默认 /srv/service-hub-data，容器内固定为 /data。
- Hub API 和 conda Runner 不得获得 Docker Socket 权限，只有 Docker Runner 可以访问。
- 不接受 chmod 666 /var/run/docker.sock。
- V1 只接受可信内部插件，不宣称 conda-pack 是不可信代码沙箱。
- 长期运行 Docker 服务插件不在本计划内。
- 所有行为修改遵循测试先行，每个任务单独提交。

---

## 文件结构与职责

本计划新增或修改的主要文件：

~~~text
Dockerfile
    统一运行镜像，安装四个内部包、tini、supervisord 和 Docker SDK。

compose.yaml
    唯一 service-hub 服务、一个数据挂载、一个 Socket 挂载。

deploy/service_hub/bootstrap.py
    校验宿主路径、初始化数据目录、Token 和运行时环境文件。

deploy/service_hub/entrypoint.sh
    动态 Socket GID、权限迁移、数据库迁移、启动 supervisord。

deploy/service_hub/supervisord.conf
    三个降权业务进程的启动、重启、停止与日志配置。

deploy/service_hub/healthcheck.py
    检查 API、三个受管进程、数据目录和 Docker Engine。

deploy/runner/service_common.py
    两个 Runner 共享的内部 HTTP 请求和启动退避逻辑。

tools/hubctl
    基于 Python 标准库的宿主机管理客户端。

packages/hub-publisher/
    通用 conda-pack/Docker .pypkg 构建库与 hub-plugin 命令。

templates/conda-script-plugin/
templates/docker-job-plugin/
    新业务脚本的最小可复制模板。

deploy/release/
    离线镜像打包、安装、启动、停止和状态脚本。

tests/deploy/
    bootstrap、healthcheck、发布脚本和权限测试。

tests/tools/
    hubctl 与 hub-plugin 命令测试。
~~~

---

### Task 1: 让两个 Runner 支持通用插件和单容器启动重试

**Files:**

- Create: deploy/runner/service_common.py
- Modify: deploy/runner/conda_runner_service.py
- Modify: deploy/runner/docker_runner_service.py
- Modify: packages/hub-runner/src/hub_runner/conda_executor.py
- Test: tests/runner/test_runner_service_common.py
- Test: tests/runner/test_conda_executor.py
- Test: tests/runner/test_docker_executor.py

**Interfaces:**

- Produces: post_json(base_url: str, token: str, path: str, payload: dict[str, Any]) -> dict[str, Any] | None
- Produces: retry_startup(action: Callable[[], None], sleep: Callable[[float], None], delay_seconds: float = 2.0) -> None
- Preserves: 两个服务模块现有 _post 名称作为可测试兼容别名。
- Changes: conda Build 健康检查只导入 hub_runner、python_hub_sdk、python_hub_contracts。
- Changes: conda Runner 收到 SIGTERM 时终止当前插件进程组，不遗留孤儿进程。

- [ ] **Step 1: 为通用 conda 健康检查写失败测试**

在现有 test_conda_executor_runs_conda_unpack_then_import_healthcheck 中，把
fake_runner.calls 的第二条命令期望值改为：

~~~python
assert fake_runner.calls[1] == [
    str(env_root / "bin" / "python"),
    "-c",
    "import hub_runner, python_hub_contracts, python_hub_sdk",
]
assert "h5py" not in " ".join(fake_runner.calls[1])
assert "osgeo" not in " ".join(fake_runner.calls[1])
~~~

- [ ] **Step 2: 运行测试并确认旧硬编码导致失败**

Run:

~~~bash
python -m pytest tests/runner/test_conda_executor.py -q
~~~

Expected: FAIL，实际命令仍包含 h5py、scipy 和 osgeo。

- [ ] **Step 3: 实现通用环境健康检查**

将 CondaExecutor.install 中的检查命令替换为：

~~~python
healthcheck = self._run(
    [
        str(python),
        "-c",
        "import hub_runner, python_hub_contracts, python_hub_sdk",
    ],
    timeout=build.timeout_seconds,
)
~~~

- [ ] **Step 4: 为启动退避写失败测试**

创建 tests/runner/test_runner_service_common.py：

~~~python
from urllib.error import URLError

from deploy.runner.service_common import retry_startup


def test_retry_startup_retries_connection_errors() -> None:
    attempts = 0
    delays: list[float] = []

    def action() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise URLError("not ready")

    retry_startup(action, sleep=delays.append, delay_seconds=0.25)

    assert attempts == 3
    assert delays == [0.25, 0.25]
~~~

- [ ] **Step 5: 运行测试确认公共模块不存在**

Run:

~~~bash
python -m pytest tests/runner/test_runner_service_common.py -q
~~~

Expected: FAIL with ModuleNotFoundError for deploy.runner.service_common。

- [ ] **Step 6: 实现公共请求和退避函数**

service_common.py 的核心实现：

~~~python
def retry_startup(
    action: Callable[[], None],
    *,
    sleep: Callable[[float], None] = time.sleep,
    delay_seconds: float = 2.0,
) -> None:
    if delay_seconds <= 0:
        raise ValueError("delay_seconds must be positive")
    while True:
        try:
            action()
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            sleep(delay_seconds)
~~~

将原有重复的 _post 请求逻辑移到 post_json，保留：

~~~python
_post = post_json
~~~

两个 Runner 在首次 reconcile 时调用 retry_startup；正常轮询中的协议错误仍直接暴露，
不吞掉 HTTP 4xx。

- [ ] **Step 7: 增加 conda Runner 停止清理**

为 _run_job_process 增加 try/finally：任何 BaseException 离开控制循环时，只要子进程仍在
运行，就调用 _terminate_process_group 后再重新抛出。conda_runner_service 与 Docker
Runner 一样注册 SIGTERM 处理器。测试让 cancellation_requested 抛出 SystemExit，
断言插件进程组收到 SIGTERM、必要时收到 SIGKILL，并且异常继续向上传播。

- [ ] **Step 8: 运行 Runner 全部测试**

Run:

~~~bash
python -m pytest tests/runner -q
python -m ruff check deploy/runner packages/hub-runner tests/runner
~~~

Expected: PASS。

- [ ] **Step 9: 提交**

~~~bash
git add deploy/runner packages/hub-runner/src/hub_runner/conda_executor.py tests/runner
git commit -m "fix: make runtime runners generic and startup tolerant"
~~~

---

### Task 2: 实现单容器启动配置、Token 和路径校验

**Files:**

- Create: deploy/service_hub/__init__.py
- Create: deploy/service_hub/bootstrap.py
- Test: tests/deploy/test_bootstrap.py

**Interfaces:**

- Produces: validate_host_data_dir(value: str) -> Path
- Produces: load_or_create_runner_token(data_root: Path, explicit: str | None) -> str
- Produces: docker_socket_gid(socket_path: Path) -> int
- Produces: write_runtime_environment(destination: Path, token: str) -> None
- Produces CLI: python -m deploy.service_hub.bootstrap

- [ ] **Step 1: 写宿主路径失败测试**

~~~python
import pytest

from deploy.service_hub.bootstrap import validate_host_data_dir


@pytest.mark.parametrize("value", ["", ".", "data", "/", "/srv", "/tmp"])
def test_validate_host_data_dir_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        validate_host_data_dir(value)


def test_validate_host_data_dir_accepts_deployment_path() -> None:
    assert validate_host_data_dir("/srv/service-hub-data").as_posix() == (
        "/srv/service-hub-data"
    )
~~~

- [ ] **Step 2: 写 Token 持久化失败测试**

~~~python
from deploy.service_hub.bootstrap import load_or_create_runner_token


def test_generated_token_is_private_and_stable(tmp_path) -> None:
    first = load_or_create_runner_token(tmp_path, None)
    second = load_or_create_runner_token(tmp_path, None)

    token_file = tmp_path / "secrets" / "runner-token"
    assert first == second
    assert len(first) >= 64
    assert token_file.stat().st_mode & 0o777 == 0o600


def test_explicit_token_does_not_replace_persisted_token(tmp_path) -> None:
    assert load_or_create_runner_token(tmp_path, "x" * 64) == "x" * 64
    assert not (tmp_path / "secrets" / "runner-token").exists()
~~~

- [ ] **Step 3: 运行测试确认模块不存在**

Run:

~~~bash
python -m pytest tests/deploy/test_bootstrap.py -q
~~~

Expected: FAIL with ModuleNotFoundError。

- [ ] **Step 4: 实现 bootstrap 纯函数**

要求：

- validate_host_data_dir 使用 Path(value)，要求 POSIX 绝对路径；
- 拒绝 /、/srv、/tmp、/var、/data 和少于三级的路径；
- Token 使用 secrets.token_hex(32)；
- secrets 目录权限 0700，Token 文件通过 O_CREAT | O_EXCL 和 0600 原子创建；
- docker_socket_gid 使用 Path.stat().st_gid，非 Socket 路径抛 ValueError；
- runtime.env 写入 HUB_RUNNER_TOKEN 单行，文件权限 0600；
- 日志和标准输出不得包含 Token。

- [ ] **Step 5: 增加 CLI 测试**

使用 monkeypatch 设置 HUB_HOST_DATA_DIR、HUB_RUNNER_TOKEN、HUB_DATA_ROOT 和
HUB_RUNTIME_ENV，调用 main()，断言退出码 0、数据子目录存在、runtime.env 权限为 0600。

- [ ] **Step 6: 运行测试和类型检查**

~~~bash
python -m pytest tests/deploy/test_bootstrap.py -q
python -m ruff check deploy/service_hub tests/deploy/test_bootstrap.py
python -m mypy deploy/service_hub
~~~

Expected: PASS。

- [ ] **Step 7: 提交**

~~~bash
git add deploy/service_hub tests/deploy/test_bootstrap.py
git commit -m "feat: add single-container bootstrap"
~~~

---

### Task 3: 构建统一镜像并管理三个降权进程

**Files:**

- Modify: Dockerfile
- Replace: docker-entrypoint.sh
- Create: deploy/service_hub/supervisord.conf
- Create: deploy/service_hub/healthcheck.py
- Test: tests/deploy/test_single_container_files.py
- Test: tests/deploy/test_healthcheck.py

**Interfaces:**

- Consumes: bootstrap CLI 和 /run/service-hub/runtime.env。
- Produces: Docker ENTRYPOINT 为 /usr/bin/tini -- /usr/local/bin/service-hub-entrypoint。
- Produces: HEALTHCHECK 调用 python -m deploy.service_hub.healthcheck。
- Produces: check_health(process_states: Mapping[str, str], api_up: bool, data_writable: bool, docker_up: bool) -> None。

- [ ] **Step 1: 写统一镜像静态失败测试**

~~~python
from pathlib import Path


def test_runtime_image_contains_all_components() -> None:
    dockerfile = Path("Dockerfile").read_text("utf-8")
    assert "packages/hub-server" in dockerfile
    assert "packages/hub-runner" in dockerfile
    assert "packages/hub-sdk" in dockerfile
    assert "supervisord.conf" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert 'ENTRYPOINT ["/usr/bin/tini"' in dockerfile


def test_supervisor_runs_three_explicit_users() -> None:
    config = Path("deploy/service_hub/supervisord.conf").read_text("utf-8")
    assert "[program:hub-api]" in config
    assert "[program:conda-runner]" in config
    assert "[program:docker-runner]" in config
    assert "user=hub-api" in config
    assert "user=conda-runner" in config
    assert "user=docker-runner" in config
~~~

- [ ] **Step 2: 运行静态测试确认失败**

~~~bash
python -m pytest tests/deploy/test_single_container_files.py -q
~~~

Expected: FAIL，因为统一 Supervisor 配置尚不存在。

- [ ] **Step 3: 重写 Dockerfile**

Dockerfile 必须：

- apt 安装 tini、supervisor、gosu 和最小运行依赖；
- pip 安装 hub-contracts、hub-sdk、hub-runner、hub-server；
- 创建 hub-data 共享组以及 hub-api、conda-runner、docker-runner 三个用户；
- 复制 Alembic、默认配置、Runner 服务和 deploy/service_hub；
- 不复制 NC 插件；
- 不安装 Conda、GDAL 或 Docker Daemon；
- 保持 Python 3.12；
- 设置 stop signal SIGTERM 和 60 秒以上停止宽限建议；
- 声明统一 ENTRYPOINT、CMD 和 HEALTHCHECK。

- [ ] **Step 4: 实现 entrypoint 与 Supervisor**

entrypoint.sh 执行：

~~~sh
python -m deploy.service_hub.bootstrap
. /run/service-hub/runtime.env
export HUB_RUNNER_TOKEN
export HUB_INTERNAL_BASE_URL=http://127.0.0.1:8000/internal/v1
export HUB_DATA_ROOT=/data
export HUB_DOCKER_HOST_DATA_ROOT="$HUB_HOST_DATA_DIR"
exec supervisord -n -c /etc/service-hub/supervisord.conf
~~~

在调用 supervisord 前，脚本根据 stat -c %g /var/run/docker.sock 将 docker-runner 加入
对应组，并执行一次版本化权限迁移：

- /data/db、files、plugins、jobs 属于 hub-api:hub-data；
- /data/environments 属于 conda-runner:hub-data；
- 目录组写和 setgid；
- 生成 /data/.permissions-single-container-v1 后不再全量递归迁移。

Supervisor 为每个进程设置 umask=0002、autorestart=true、stopasgroup=true、
killasgroup=true，并将 stdout/stderr 转发到容器日志。

- [ ] **Step 5: 写健康检查失败测试**

将系统探测与状态判断分离，分别验证：

~~~python
def test_health_requires_all_processes_running() -> None:
    with pytest.raises(HealthcheckError, match="conda-runner"):
        check_health(
            process_states={
                "hub-api": "RUNNING",
                "conda-runner": "FATAL",
                "docker-runner": "RUNNING",
            },
            api_up=True,
            data_writable=True,
            docker_up=True,
        )


def test_health_requires_writable_data() -> None:
    with pytest.raises(HealthcheckError, match="/data"):
        check_health(
            process_states={
                "hub-api": "RUNNING",
                "conda-runner": "RUNNING",
                "docker-runner": "RUNNING",
            },
            api_up=True,
            data_writable=False,
            docker_up=True,
        )


def test_health_requires_docker_ping() -> None:
    with pytest.raises(HealthcheckError, match="Docker"):
        check_health(
            process_states={
                "hub-api": "RUNNING",
                "conda-runner": "RUNNING",
                "docker-runner": "RUNNING",
            },
            api_up=True,
            data_writable=True,
            docker_up=False,
        )
~~~

- [ ] **Step 6: 实现 healthcheck**

healthcheck.py 使用 supervisor XML-RPC 查询三个进程状态，urllib 请求
/api/v1/system/health，创建后删除 /data/.healthcheck 临时文件，并通过 Docker SDK
ping。任一步失败均打印不含秘密的单行原因并退出 1，全部成功退出 0。

- [ ] **Step 7: 运行任务测试**

~~~bash
python -m pytest tests/deploy -q
python -m ruff check deploy/service_hub tests/deploy
python -m mypy deploy/service_hub
~~~

Expected: PASS。

- [ ] **Step 8: 构建镜像**

~~~bash
docker build -t python-service-hub:1.0.0-linux-amd64 .
docker image inspect python-service-hub:1.0.0-linux-amd64
~~~

Expected: 构建成功且只有一个 Hub 运行镜像。

- [ ] **Step 9: 提交**

~~~bash
git add Dockerfile docker-entrypoint.sh deploy/service_hub tests/deploy
git commit -m "feat: run hub and both runners in one container"
~~~

---

### Task 4: 将 Compose 和容器冒烟测试改为单服务

**Files:**

- Modify: compose.yaml
- Modify: tests/server/test_container_smoke.py
- Modify: tests/runner/test_conda_executor.py
- Modify: tests/runner/test_docker_executor.py
- Delete: deploy/runner/Dockerfile.conda-runner
- Delete: deploy/runner/Dockerfile.docker-runner

**Interfaces:**

- Consumes: Task 3 的统一 Dockerfile。
- Produces: Compose service 名 service-hub。
- Produces: HUB_HOST_DATA_DIR 是唯一必填部署变量。

- [ ] **Step 1: 将 Compose 断言改为目标拓扑并确认失败**

~~~python
def test_production_compose_has_one_long_running_service() -> None:
    model = yaml.safe_load(Path("compose.yaml").read_text("utf-8"))
    assert set(model["services"]) == {"service-hub"}
    service = model["services"]["service-hub"]
    assert service["ports"] == ["127.0.0.1:8000:8000"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in service["volumes"]
    assert service["environment"]["HUB_HOST_DATA_DIR"]
~~~

Run:

~~~bash
python -m pytest tests/server/test_container_smoke.py -q
~~~

Expected: FAIL，旧 Compose 仍包含三个服务。

- [ ] **Step 2: 替换 compose.yaml**

Compose 只定义 service-hub，使用统一镜像、linux/amd64、回环端口、restart
unless-stopped、HUB_HOST_DATA_DIR、数据 bind 和 Docker Socket bind。不得声明
hub-conda-runner 或 hub-docker-runner。

- [ ] **Step 3: 更新一次性冒烟 Compose 生成器**

_write_smoke_compose 只输出一个服务，并将临时数据绝对路径同时写入环境变量和 volume
source。测试不得与生产 8000 端口冲突。

- [ ] **Step 4: 删除旧 Runner Dockerfile 和旧拓扑断言**

Runner Python 入口保留；只删除两个不再被构建的 Dockerfile。将 Runner 测试中的 Compose
断言改为检查统一服务包含两个入口和唯一 Socket 挂载。

- [ ] **Step 5: 解析 Compose**

~~~bash
HUB_HOST_DATA_DIR=/srv/service-hub-data docker compose config --quiet
~~~

Expected: exit 0，解析结果只有 service-hub。

- [ ] **Step 6: 在 Linux AMD64 执行容器冒烟测试**

~~~bash
python -m pytest tests/server/test_container_smoke.py -m integration -v
~~~

Expected: 单容器变为 healthy，health 返回 status UP，文件上传返回 201。

- [ ] **Step 7: 提交**

~~~bash
git add compose.yaml deploy/runner tests/server/test_container_smoke.py tests/runner
git commit -m "feat: deploy service hub as one compose service"
~~~

---

### Task 5: 制作可重复执行的 Linux AMD64 离线发布包

**Files:**

- Create: deploy/release/build-release.sh
- Create: deploy/release/install.sh
- Create: deploy/release/start.sh
- Create: deploy/release/stop.sh
- Create: deploy/release/status.sh
- Test: tests/deploy/test_release_scripts.py

**Interfaces:**

- Produces: dist/service-hub-1.0.0-linux-amd64.tar.gz
- Consumes: python-service-hub:1.0.0-linux-amd64。
- Uses: HUB_HOST_DATA_DIR，默认 /srv/service-hub-data。

- [ ] **Step 1: 写发布文件集合失败测试**

~~~python
def test_release_scripts_are_safe_and_idempotent() -> None:
    names = {"build-release.sh", "install.sh", "start.sh", "stop.sh", "status.sh"}
    root = Path("deploy/release")
    assert names <= {path.name for path in root.iterdir()}
    for name in names:
        source = (root / name).read_text("utf-8")
        assert "rm -rf" not in source
        assert "docker compose down -v" not in source
~~~

- [ ] **Step 2: 运行测试确认脚本不存在**

~~~bash
python -m pytest tests/deploy/test_release_scripts.py -q
~~~

Expected: FAIL。

- [ ] **Step 3: 实现 build-release.sh**

脚本固定步骤：

1. 检查当前架构为 x86_64 或 amd64；
2. docker build 统一镜像；
3. docker save 到 service-hub-image.tar；
4. 复制 compose.yaml、安装脚本和 hubctl；
5. sha256sum 生成 SHA256SUMS；
6. tar -czf 生成最终发布包；
7. 输出发布包路径和摘要。

临时目录使用 mktemp -d 并通过 trap 清理，不删除 dist 中已有其他文件。

- [ ] **Step 4: 实现安装和生命周期脚本**

install.sh：

- 校验 SHA256SUMS；
- 检查 docker、docker compose 和 python3；
- install -d 创建 mode 0750 的绝对数据目录；
- docker load 镜像；
- 写入只包含 HUB_HOST_DATA_DIR 的 .env；
- 已存在 .env 时保留原值。

start.sh 使用 env 中路径验证目录后执行 docker compose up -d；stop.sh 只执行
docker compose stop；status.sh 输出 compose ps、容器 health 和 Hub HTTP health。

- [ ] **Step 5: 运行 Shell 语法和静态安全测试**

~~~bash
for file in deploy/release/*.sh; do bash -n "$file"; done
python -m pytest tests/deploy/test_release_scripts.py -q
~~~

Expected: PASS。

- [ ] **Step 6: 构建发布包并核对内容**

~~~bash
bash deploy/release/build-release.sh
tar -tzf dist/service-hub-1.0.0-linux-amd64.tar.gz
~~~

Expected: 包中包含镜像 tar、SHA256SUMS、单服务 Compose 和全部脚本。

- [ ] **Step 7: 提交**

~~~bash
git add deploy/release tests/deploy/test_release_scripts.py
git commit -m "feat: add offline single-container release bundle"
~~~

---

### Task 6: 提供无第三方 Python 依赖的 hubctl

**Files:**

- Create: tools/hubctl
- Test: tests/tools/test_hubctl.py

**Interfaces:**

- Produces CLI: health、plugin list/install/enable、file upload、job run/status/logs/cancel/download。
- Uses: HUB_URL，默认 http://127.0.0.1:8000。
- Produces: main(argv: Sequence[str] | None = None, transport: Transport | None = None) -> int。
- Produces Transport methods: request_json(method: str, path: str, body: object | None) -> object；upload(path: str, file_path: Path) -> object；download(path: str, destination: Path) -> None。

- [ ] **Step 1: 写参数解析失败测试**

~~~python
def test_job_run_builds_current_api_payload(fake_transport, tmp_path) -> None:
    params = tmp_path / "params.json"
    params.write_text('{"group_name":"1"}', encoding="utf-8")

    exit_code = hubctl.main(
        [
            "job", "run", "nc_to_shp",
            "--version", "1.0.0",
            "--runtime", "conda-pack",
            "--input", "source_nc=file_123",
            "--params", str(params),
        ],
        transport=fake_transport,
    )

    assert exit_code == 0
    assert fake_transport.json_body == {
        "plugin_id": "nc_to_shp",
        "version": "1.0.0",
        "runtime_type": "conda-pack",
        "inputs": {"source_nc": "file_123"},
        "params": {"group_name": "1"},
    }
~~~

- [ ] **Step 2: 写上传、安装和下载失败测试**

使用 FakeTransport 断言：

- plugin install 使用 multipart POST /api/v1/plugins/install；
- file upload 使用 multipart POST /api/v1/files；
- job download 先 GET outputs，再逐个 GET /api/v1/files/FILE_ID/download；
- 非 2xx 响应打印 Hub 错误 code/message 并返回 1；
- 输出文件存在时拒绝覆盖。

- [ ] **Step 3: 运行测试确认工具不存在**

~~~bash
python -m pytest tests/tools/test_hubctl.py -q
~~~

Expected: FAIL。

- [ ] **Step 4: 实现标准库客户端**

tools/hubctl 使用 argparse、json、http.client、urllib.parse、uuid 和 pathlib；
自行生成 multipart 边界与 Content-Length，并通过 HTTPConnection.send 按块传输文件，
不得把大文件整体读入内存，不引入 requests/httpx。每个成功命令向 stdout 输出格式化
JSON，错误写 stderr。脚本包含 python3 shebang 并设置可执行权限。

- [ ] **Step 5: 运行测试和帮助命令**

~~~bash
python -m pytest tests/tools/test_hubctl.py -q
python tools/hubctl --help
python tools/hubctl job run --help
python -m ruff check tools/hubctl tests/tools/test_hubctl.py
~~~

Expected: PASS，帮助中列出全部命令。

- [ ] **Step 6: 对运行中的测试 Hub 做冒烟调用**

~~~bash
HUB_URL=http://127.0.0.1:8000 python tools/hubctl health
HUB_URL=http://127.0.0.1:8000 python tools/hubctl plugin list
~~~

Expected: exit 0 并输出合法 JSON。

- [ ] **Step 7: 提交**

~~~bash
git add tools/hubctl tests/tools/test_hubctl.py
git commit -m "feat: add simple service hub command client"
~~~

---

### Task 7: 将 NC 专用构建逻辑提炼为通用 hub-plugin

**Files:**

- Create: packages/hub-publisher/pyproject.toml
- Create: packages/hub-publisher/src/hub_publisher/__init__.py
- Create: packages/hub-publisher/src/hub_publisher/project.py
- Create: packages/hub-publisher/src/hub_publisher/builder.py
- Create: packages/hub-publisher/src/hub_publisher/archive.py
- Create: packages/hub-publisher/src/hub_publisher/cli.py
- Modify: packages/nc-to-shp-plugin/scripts/package_build.py
- Modify: pyproject.toml
- Test: tests/publisher/test_project.py
- Test: tests/publisher/test_builder.py
- Test: tests/publisher/test_archive.py
- Modify: tests/plugins/test_package_build.py

**Interfaces:**

- Produces: PluginProject.load(path: Path) -> PluginProject。
- Produces: build_plugin(project: PluginProject, runtime_type: RuntimeType, arch: Architecture, output_dir: Path) -> Path。
- Produces: create_plugin_package(project: PluginProject, build: PluginBuildManifest, runtime_archive: Path, output_dir: Path, source_date_epoch: int) -> Path。
- Produces console command: hub-plugin build PROJECT --runtime RUNTIME --arch ARCH --output DIR。
- Preserves: NC package_build.py 原有参数和规范文件名。

- [ ] **Step 1: 写项目目录校验失败测试**

~~~python
def test_project_requires_manifest_source_and_selected_runtime(tmp_path) -> None:
    (tmp_path / "plugin.yaml").write_text(VALID_MANIFEST, encoding="utf-8")
    (tmp_path / "src").mkdir()

    project = PluginProject.load(tmp_path)

    with pytest.raises(ValueError, match="environment.yml"):
        project.validate_runtime("conda-pack")
    with pytest.raises(ValueError, match="Dockerfile"):
        project.validate_runtime("docker")
~~~

- [ ] **Step 2: 写确定性归档失败测试**

固定 SOURCE_DATE_EPOCH=0，两次调用 create_plugin_package，断言字节 SHA256 完全一致；
断言包只含 plugin.yaml、build.json、src 和选定 runtime archive，拒绝软链接、绝对路径
和路径穿越。

- [ ] **Step 3: 运行 Publisher 测试确认包不存在**

~~~bash
python -m pytest tests/publisher -q
~~~

Expected: FAIL with ModuleNotFoundError for hub_publisher。

- [ ] **Step 4: 实现项目和归档层**

PluginProject.load：

- resolve 项目根；
- 用现有 load_plugin_manifest 校验 plugin.yaml；
- 要求 src 为真实目录且不含符号链接；
- conda 配置优先使用项目根 environment.yml，并兼容现有 conda/environment.yml；
- Docker 配置优先使用项目根 Dockerfile，并兼容现有 docker/Dockerfile。

archive.py 复用现有 PAX tar、mtime、uid/gid、mode、排序和 zstd 规则，生成现有
PluginBuildManifest，保持安装端完全兼容。

- [ ] **Step 5: 写构建命令测试**

FakeCommandRunner 断言：

- conda 执行 conda env create、conda run conda-pack；
- Docker 执行 docker build、docker image inspect、docker image save；
- 两种构建都先生成 hub-contracts、hub-sdk、hub-runner wheels；
- 架构必须为 amd64 或 arm64；
- conda 构建要求当前 Linux 原生架构相同；
- Docker 构建明确传 --platform linux/amd64 或 linux/arm64。

- [ ] **Step 6: 实现 builder 与 CLI**

builder.py 接受注入的 CommandRunner，构建过程使用临时目录并在失败后清理。Docker
上下文固定包含 plugin.yaml、src/plugin、wheelhouse 和用户 Dockerfile。Conda 创建
环境时设置 PIP_FIND_LINKS 和 PIP_NO_INDEX，使 Hub wheels 来自本地 wheelhouse。

同时在根 pyproject.toml 的 mypy packages 增加 hub_publisher，在 mypy_path 增加
packages/hub-publisher/src，确保发布者包进入统一静态检查。

CLI 成功时打印最终包绝对路径和 SHA256，失败返回非零且不留下半成品。

- [ ] **Step 7: 将 NC 构建脚本变成兼容包装器**

package_build.py 保持已有命令行参数，内部构造 PluginProject 并调用 build_plugin。
原有 tests/plugins/test_package_build.py 全部继续通过，两个文件名不变。

- [ ] **Step 8: 运行完整构建器测试**

~~~bash
python -m pytest tests/publisher tests/plugins/test_package_build.py -q
python -m ruff check packages/hub-publisher packages/nc-to-shp-plugin/scripts tests/publisher
python -m mypy packages/hub-publisher/src/hub_publisher
~~~

Expected: PASS。

- [ ] **Step 9: 提交**

~~~bash
git add packages/hub-publisher packages/nc-to-shp-plugin/scripts/package_build.py pyproject.toml tests/publisher tests/plugins/test_package_build.py
git commit -m "feat: add generic dual-runtime plugin publisher"
~~~

---

### Task 8: 增加两种通用插件模板

**Files:**

- Create: templates/conda-script-plugin/plugin.yaml
- Create: templates/conda-script-plugin/environment.yml
- Create: templates/conda-script-plugin/src/example_plugin/main.py
- Create: templates/docker-job-plugin/plugin.yaml
- Create: templates/docker-job-plugin/Dockerfile
- Create: templates/docker-job-plugin/src/example_plugin/main.py
- Test: tests/publisher/test_templates.py

**Interfaces:**

- Consumes: Task 7 hub-plugin。
- Produces: 两个可直接复制、改名和构建的最小项目。
- Preserves: 入口签名 params、inputs、context 三参数。

- [ ] **Step 1: 写模板契约失败测试**

~~~python
@pytest.mark.parametrize(
    ("template", "runtime"),
    [
        ("conda-script-plugin", "conda-pack"),
        ("docker-job-plugin", "docker"),
    ],
)
def test_template_is_valid_plugin_project(template: str, runtime: str) -> None:
    project = PluginProject.load(Path("templates") / template)
    project.validate_runtime(runtime)
    assert project.manifest.entrypoint.function == "run"
~~~

- [ ] **Step 2: 运行测试确认模板不存在**

~~~bash
python -m pytest tests/publisher/test_templates.py -q
~~~

Expected: FAIL。

- [ ] **Step 3: 创建最小业务示例**

两个 main.py 都实现：

~~~python
def run(
    params: dict[str, object],
    inputs: dict[str, InputFile | list[InputFile]],
    context: PluginContext,
) -> PluginResult:
    message = str(params.get("message", "hello"))
    destination = context.output_file("result.txt", create_parent=True)
    destination.write_text(message + "\n", encoding="utf-8")
    return PluginResult(
        message="处理完成",
        data={"message": message},
        files=[OutputFile(name="result", path="result.txt", format="text/plain")],
    )
~~~

plugin.yaml 声明 message 参数和 result 输出；conda environment.yml 固定 Python 3.12、
conda-pack、Pydantic、PyYAML、zstandard 和三个本地 Hub wheel；Dockerfile 使用
Python 3.12 slim、安装 wheelhouse、非 root 65532 启动 python -m hub_runner。

- [ ] **Step 4: 测试模板入口**

使用现有 run_job 测试辅助生成 job.json，分别加载两个模板源码，断言 result.json 为
SUCCESS 且 result.txt 内容正确。

- [ ] **Step 5: 运行测试**

~~~bash
python -m pytest tests/publisher/test_templates.py tests/runner/test_execution.py -q
python -m ruff check templates tests/publisher/test_templates.py
~~~

Expected: PASS。

- [ ] **Step 6: 提交**

~~~bash
git add templates tests/publisher/test_templates.py
git commit -m "feat: add conda and docker script plugin templates"
~~~

---

### Task 9: 更新迁移、部署和日常使用文档

**Files:**

- Modify: README.md
- Create: docs/guides/单容器部署与脚本插件使用.md
- Modify: docs/guides/Linux-AMD64-首次部署与NC插件验收清单.md
- Modify: docs/guides/项目总览与实施部署.md
- Modify: docs/guides/双运行时插件构建与部署.md
- Test: tests/deploy/test_documented_commands.py

**Interfaces:**

- Documents: 在线构建、离线导入、单命令启动、旧数据迁移、hubctl、hub-plugin、
  conda/Docker 模板和 NC 验收。

- [ ] **Step 1: 写文档命令一致性失败测试**

测试提取关键文件并断言：

- 不再出现需要启动三个 Compose 服务的主流程；
- 所有主流程使用 HUB_HOST_DATA_DIR；
- 镜像名是 python-service-hub:1.0.0-linux-amd64；
- 文档包含 hubctl plugin install、hubctl job run 和 hub-plugin build；
- 迁移章节包含备份和禁止 down -v；
- README 链接指向新指南。

- [ ] **Step 2: 运行测试确认旧文档失败**

~~~bash
python -m pytest tests/deploy/test_documented_commands.py -q
~~~

Expected: FAIL。

- [ ] **Step 3: 编写单容器主指南**

必须按实际执行顺序提供：

1. 安装 Docker；
2. 导入离线镜像或源码 build；
3. 设置数据绝对路径；
4. 一条 Compose 命令启动；
5. health、logs、stop、restart；
6. 使用 hubctl 注册和运行插件；
7. 使用 hub-plugin 创建两种脚本；
8. Nginx 边界；
9. 备份、升级和旧数据迁移；
10. 常见错误及对应检查命令。

- [ ] **Step 4: 修订旧指南**

旧三容器内容保留为历史说明时必须明确标记“旧拓扑，不再作为部署入口”；所有首页入口
优先指向单容器指南。删除相互矛盾的镜像数量、服务名和 Token 手工配置说明。

- [ ] **Step 5: 验证 Markdown**

~~~bash
python -m pytest tests/deploy/test_documented_commands.py -q
git diff --check
~~~

Expected: PASS，无失效相对链接和不平衡代码围栏。

- [ ] **Step 6: 提交**

~~~bash
git add README.md docs/guides tests/deploy/test_documented_commands.py
git commit -m "docs: document single-container deployment and plugin workflow"
~~~

---

### Task 10: 完成单容器双运行时发布验收

**Files:**

- Modify: tests/integration/test_dual_runtime_nc_to_shp.py
- Create: tests/integration/test_single_container_lifecycle.py
- Create: docs/reports/single-container-linux-amd64-acceptance.md

**Interfaces:**

- Consumes: 单镜像、单服务 Compose、两个 NC .pypkg、真实 NC 文件。
- Produces: Linux AMD64 发布证据和最终验收结论。

- [ ] **Step 1: 更新集成测试的部署前置断言**

测试启动前解析 docker compose config --services，断言输出只有 service-hub；检查容器
health 为 healthy，容器重启前后 health 均恢复。

- [ ] **Step 2: 增加生命周期测试**

test_single_container_lifecycle.py 执行：

- 启动空数据目录；
- 上传小文件并保存 file_id；
- docker compose restart；
- 等待健康；
- GET File 元数据仍返回 200；
- 检查 supervisor 三个进程为 RUNNING；
- 在 hub-api 和 conda-runner 身份执行 Docker Socket 访问得到 permission denied；
- 在 docker-runner 身份执行 Docker ping 成功。

- [ ] **Step 3: 运行非集成发布门**

~~~bash
python -m pytest -m "not integration"
python -m ruff check .
python -m mypy packages
HUB_HOST_DATA_DIR=/srv/service-hub-data docker compose config --quiet
~~~

Expected: 全部 PASS。

- [ ] **Step 4: 在原生 Linux AMD64 构建两个 NC 插件包**

~~~bash
hub-plugin build packages/nc-to-shp-plugin --runtime conda-pack --arch amd64 --output packages/nc-to-shp-plugin/dist
hub-plugin build packages/nc-to-shp-plugin --runtime docker --arch amd64 --output packages/nc-to-shp-plugin/dist
sha256sum packages/nc-to-shp-plugin/dist/*.pypkg
~~~

Expected: 生成固定名称的 conda 和 docker 两个包。

- [ ] **Step 5: 执行真实 NC 双运行时验收**

~~~bash
HUB_NC_SAMPLE_PATH=/srv/python-service-hub/fixtures/sample.nc \
HUB_PLUGIN_PACKAGE_DIR=/srv/python-service-hub/packages/nc-to-shp-plugin/dist \
HUB_BASE_URL=http://127.0.0.1:8000 \
python -m pytest -m integration tests/integration/test_dual_runtime_nc_to_shp.py -v
~~~

Expected: 两个 Job 为 SUCCESS；两个 ZIP 组件、PointZ、CRS、字段、SMID、坐标和属性
一致；每个 Shapefile 39,464 个要素。

- [ ] **Step 6: 执行容器生命周期验收**

~~~bash
python -m pytest -m integration tests/integration/test_single_container_lifecycle.py -v
~~~

Expected: PASS，重启后数据保持，权限边界符合设计。

- [ ] **Step 7: 构建并验证离线发布包**

~~~bash
bash deploy/release/build-release.sh
mkdir -p /tmp/service-hub-release-check
tar -xzf dist/service-hub-1.0.0-linux-amd64.tar.gz -C /tmp/service-hub-release-check
cd /tmp/service-hub-release-check/service-hub-linux-amd64
sha256sum -c SHA256SUMS
sudo ./install.sh
sudo ./start.sh
sudo ./status.sh
~~~

Expected: SHA256 全部 OK，单容器 healthy。

- [ ] **Step 8: 填写验收报告**

报告记录：

- Git commit；
- Linux 版本和 uname -m；
- Docker 与 Compose 版本；
- Hub 镜像 ID 和 SHA256；
- 两个插件包 SHA256；
- build_id、file_id、两个 job_id；
- 两份输出 SHA256；
- 39,464 要素比较结果；
- 重启持久化结果；
- 权限测试结果；
- 发布包 SHA256；
- 未通过项必须为空。

- [ ] **Step 9: 最终提交**

~~~bash
git add tests/integration docs/reports/single-container-linux-amd64-acceptance.md
git commit -m "test: certify single-container dual-runtime release"
~~~

---

## 执行顺序与检查点

检查点 A：Task 1–4 完成后，必须已有一个可启动、可执行两种 Job 的单容器核心。若该
检查点失败，不开始 CLI、模板或发布包工作。

检查点 B：Task 5–8 完成后，必须可以制作离线包、通过 hubctl 操作，并用模板创建新
脚本插件。

检查点 C：Task 9–10 完成后，文档、真实 NC 双运行时和迁移验证全部通过，才可以宣布
单容器 V1 完成。
