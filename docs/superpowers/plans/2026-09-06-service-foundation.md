# Service Hub 服务与存储基础实施计划

> **面向自主开发代理：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项实施；步骤使用复选框（`- [ ]`）记录。

**目标：** 实现可由 Docker Compose 在 Linux AMD64 启动的 Hub 基础服务，提供配置、SQLite 持久化、安全本地文件存储、健康检查和文件上传/下载 API。

**架构：** 新建 `hub-server` 包，FastAPI 应用通过显式依赖注入使用 `HubSettings`、SQLAlchemy SessionFactory 和 `LocalStorage`。文件内容写入 `/data/uploads/<file_id>/payload`，元数据保存于 SQLite；API 层仅处理 HTTP、schema 和错误映射，文件系统与数据库逻辑位于服务层。此计划不安装插件、不创建 Job、不启动插件进程。

**技术栈：** Python 3.12、FastAPI、Uvicorn、SQLAlchemy 2.x、Alembic、Pydantic 2.x、pydantic-settings、PyYAML、pytest、httpx、Docker Compose。

**设计：** `../specs/2026-09-06-executable-service-hub-mvp-design.md`

## 全局约束

- 目标为 Linux AMD64 Docker Compose；容器只监听 `0.0.0.0:8000`，Compose 只发布 `127.0.0.1:8000:8000`。
- 应用无认证，仅可部署在 Nginx、宿主机防火墙和内网边界保护之后。
- 宿主机持久化数据映射到容器 `/data`；任何 API 写入不得使用绑定主机的绝对协议路径。
- SQLite、上传文件、日志和未来插件/Job 数据均位于 `/data`；不得将大文件放入数据库。
- API 前缀固定为 `/api/v1`；失败响应固定为 `{"success": false, "error": {"code", "message", "details"}}`。
- 外部标识符使用不可预测的 `file_<uuid4 hex>`；上传原文件名只保存为元数据，磁盘内容统一命名 `payload`。
- 上传采用流式写入、SHA256 同步计算、临时目录和原子移动；拒绝超过配置上限的请求。
- 所有 SQLite 写操作必须在事务内；所有文件系统路径都先验证受控目录包含关系。
- 继续保持 `python_hub_contracts` 与 `python_hub_sdk` 无 FastAPI、SQLAlchemy、文件存储或服务端依赖。
- 每个行为先写失败测试，再写最小实现。

---

## 文件映射

```text
packages/hub-server/
├── pyproject.toml
└── src/hub_server/
    ├── __init__.py                 # 包版本
    ├── main.py                     # create_app() 和 ASGI app
    ├── settings.py                 # YAML/环境变量设置
    ├── dependencies.py             # FastAPI 依赖注入
    ├── db.py                       # Engine、SessionFactory、Base
    ├── models.py                   # FileRecord SQLAlchemy 模型
    ├── errors.py                   # HubError 和 API 异常映射
    ├── schemas.py                  # 请求/响应 Pydantic 模型
    ├── storage.py                  # LocalStorage 与安全原子写入
    ├── services/files.py            # FileService
    └── routers/
        ├── system.py               # health/info
        └── files.py                # upload/metadata/download
alembic.ini
alembic/env.py
alembic/versions/0001_file_records.py
Dockerfile
compose.yaml
config/hub.yaml.example
deploy/nginx/python-service-hub.conf.example
tests/server/
├── conftest.py
├── test_settings.py
├── test_system_api.py
├── test_storage.py
└── test_files_api.py
```

## 任务 1：Hub Server 包、配置与系统 API

**文件：**

- 新建：`packages/hub-server/pyproject.toml`
- 新建：`packages/hub-server/src/hub_server/__init__.py`
- 新建：`packages/hub-server/src/hub_server/settings.py`
- 新建：`packages/hub-server/src/hub_server/errors.py`
- 新建：`packages/hub-server/src/hub_server/schemas.py`
- 新建：`packages/hub-server/src/hub_server/main.py`
- 新建：`packages/hub-server/src/hub_server/routers/__init__.py`
- 新建：`packages/hub-server/src/hub_server/routers/system.py`
- 修改：根 `pyproject.toml`
- 新建：`tests/server/test_settings.py`
- 新建：`tests/server/test_system_api.py`

**接口：**

- 输入：`HubSettings.from_yaml(path: Path) -> HubSettings`。
- 输出：`create_app(settings: HubSettings | None = None) -> FastAPI`；`GET /api/v1/system/health`；`GET /api/v1/system/info`。

- [ ] **步骤 1：编写失败的设置和系统端点测试**

```python
def test_loads_settings_from_yaml(tmp_path: Path) -> None:
    config = tmp_path / "hub.yaml"
    config.write_text(
        "deployment:\n  mode: offline\n"
        "storage:\n  root: /var/lib/hub\n"
        "database:\n  url: sqlite:////var/lib/hub/db/hub.db\n"
        "uploads:\n  max_size_bytes: 1024\n",
        encoding="utf-8",
    )
    settings = HubSettings.from_yaml(config)
    assert settings.storage.root == Path("/var/lib/hub")
    assert settings.uploads.max_size_bytes == 1024


def test_health_and_info_report_configured_platform(client: TestClient) -> None:
    assert client.get("/api/v1/system/health").json() == {"status": "UP"}
    info = client.get("/api/v1/system/info").json()
    assert info["platform"] == {"os": "linux", "arch": "amd64"}
    assert info["deployment_mode"] == "offline"
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/server/test_settings.py tests/server/test_system_api.py -v`

预期：因 `hub_server` 包不存在而失败。

- [ ] **步骤 3：添加 Hub Server 包与依赖**

`packages/hub-server/pyproject.toml` 定义项目 `python-hub-server`、Python `>=3.12,<3.13`，运行依赖精确兼容 `fastapi>=0.115,<1`、`uvicorn[standard]>=0.30,<1`、`SQLAlchemy>=2.0,<3`、`alembic>=1.13,<2`、`pydantic-settings>=2.5,<3`、`PyYAML>=6.0,<7`。根开发依赖增加 `httpx>=0.27,<1`，并将 `packages/hub-server/src` 增加到 pytest/mypy 路径。

实现严格的嵌套设置模型：

```python
class DeploymentSettings(BaseModel):
    mode: Literal["offline"]


class StorageSettings(BaseModel):
    root: Path


class DatabaseSettings(BaseModel):
    url: str


class UploadSettings(BaseModel):
    max_size_bytes: int = Field(gt=0)


class HubSettings(BaseModel):
    deployment: DeploymentSettings
    storage: StorageSettings
    database: DatabaseSettings
    uploads: UploadSettings
    hub_version: str = "0.1.0"
    platform_os: Literal["linux"] = "linux"
    platform_arch: Literal["amd64", "arm64"] = "amd64"

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Hub 配置根节点必须是对象")
        return cls.model_validate(value)
```

使用 `yaml.safe_load()`，要求根对象为 mapping，并拒绝额外字段。`create_app()` 把 settings 放到 `app.state.settings`，注册 `/api/v1` 路由并将 `app = create_app()` 导出供 Uvicorn 使用。`info` 返回 Hub 版本、OS、架构、Python 小版本和部署模式。

- [ ] **步骤 4：运行聚焦测试和静态检查**

运行：

```text
python -m pytest tests/server/test_settings.py tests/server/test_system_api.py -v
python -m ruff check packages/hub-server tests/server
python -m mypy packages/hub-server/src
```

预期：全部通过。

- [ ] **步骤 5：提交服务骨架**

```bash
git add pyproject.toml packages/hub-server tests/server
git commit -m "feat(server): add configured system api"
```

## 任务 2：SQLite 基础、迁移与 File 元数据模型

**文件：**

- 新建：`packages/hub-server/src/hub_server/db.py`
- 新建：`packages/hub-server/src/hub_server/models.py`
- 新建：`alembic.ini`
- 新建：`alembic/env.py`
- 新建：`alembic/versions/0001_file_records.py`
- 修改：`packages/hub-server/src/hub_server/main.py`
- 新建：`tests/server/conftest.py`
- 新建：`tests/server/test_database.py`

**接口：**

- 输入：SQLite URL 和 `HubSettings`。
- 输出：`create_engine_and_session_factory(url: str) -> tuple[Engine, sessionmaker[Session]]`；`FileRecord` 表；应用启动时升级至 Alembic `head`。

- [ ] **步骤 1：编写迁移和事务测试**

```python
def test_startup_creates_file_records_table(settings: HubSettings) -> None:
    app = create_app(settings)
    with TestClient(app):
        engine = app.state.engine
        assert "file_records" in inspect(engine).get_table_names()


def test_file_record_has_unique_file_key(session: Session) -> None:
    common = {
        "scope": "UPLOAD", "role": "INPUT", "logical_name": "model.nc",
        "original_filename": "model.nc", "extension": ".nc",
        "size_bytes": 1, "sha256": "a" * 64, "status": "AVAILABLE",
        "created_at": datetime.now(UTC),
    }
    session.add_all([
        FileRecord(file_key="file_same", relative_path="uploads/a/payload", **common),
        FileRecord(file_key="file_same", relative_path="uploads/b/payload", **common),
    ])
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/server/test_database.py -v`

预期：因数据库模块、迁移或模型缺失而失败。

- [ ] **步骤 3：实现数据库与迁移**

`db.py` 使用 SQLAlchemy 2.x Declarative Base，SQLite engine 必须使用 `connect_args={"check_same_thread": False}`，并启用 SQLite foreign keys。定义：

```python
class FileRecord(Base):
    __tablename__ = "file_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    file_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    scope: Mapped[str] = mapped_column(String(16), default="UPLOAD")
    role: Mapped[str] = mapped_column(String(16), default="INPUT")
    logical_name: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    relative_path: Mapped[str] = mapped_column(String(1024), unique=True)
    extension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="AVAILABLE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
```

创建可重复执行的 Alembic 配置：`env.py` 从 `HUB_DATABASE_URL` 读取数据库 URL，`0001_file_records.py` 创建表、唯一索引和 `created_at`。应用 lifespan 在接受请求前调用 `command.upgrade(alembic_config, "head")`，并在 `app.state` 保存 Engine 和 SessionFactory。

- [ ] **步骤 4：运行验证**

运行：

```text
python -m pytest tests/server/test_database.py -v
python -m ruff check packages/hub-server alembic tests/server
python -m mypy packages/hub-server/src
```

预期：全部通过。

- [ ] **步骤 5：提交数据基础**

```bash
git add packages/hub-server alembic alembic.ini tests/server
git commit -m "feat(server): add sqlite file metadata"
```

## 任务 3：安全的本地文件存储和 FileService

**文件：**

- 新建：`packages/hub-server/src/hub_server/storage.py`
- 新建：`packages/hub-server/src/hub_server/services/__init__.py`
- 新建：`packages/hub-server/src/hub_server/services/files.py`
- 新建：`tests/server/test_storage.py`

**接口：**

- 输入：`LocalStorage(root: Path)` 和上传字节流。
- 输出：`StoredUpload(relative_path: str, size_bytes: int, sha256: str)`；`FileService.store_upload(filename: str, content_type: str | None, stream: BinaryIO) -> FileRecord`；`FileService.open_available(file_key: str) -> tuple[FileRecord, Path]`。

- [ ] **步骤 1：编写原子写入和路径逃逸失败测试**

```python
def test_store_upload_writes_payload_and_sha256(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    stored = storage.store_upload("file_abcd", io.BytesIO(b"netcdf"), max_size_bytes=10)
    assert stored.relative_path == "uploads/file_abcd/payload"
    assert (tmp_path / stored.relative_path).read_bytes() == b"netcdf"
    assert stored.sha256 == hashlib.sha256(b"netcdf").hexdigest()


def test_store_upload_removes_partial_data_when_limit_exceeded(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    with pytest.raises(UploadTooLargeError):
        storage.store_upload("file_abcd", io.BytesIO(b"01234567890"), max_size_bytes=10)
    assert not (tmp_path / "uploads/file_abcd").exists()


def test_open_relative_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).open_relative("../secret")
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/server/test_storage.py -v`

预期：因 storage/service 缺失而失败。

- [ ] **步骤 3：实现 LocalStorage 和 FileService**

在 `errors.py` 定义下列稳定异常接口；所有异常消息不得包含 Hub 的绝对路径：

```python
class HubError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class UploadTooLargeError(HubError):
    def __init__(self, *, max_size_bytes: int) -> None:
        super().__init__(
            code="UPLOAD_TOO_LARGE",
            message="上传文件超过大小限制",
            status_code=413,
            details={"max_size_bytes": max_size_bytes},
        )
```

`LocalStorage` 在构造时解析并创建 root；对每个访问的相对路径调用 `RelativeProtocolPath` 等价校验并以 `Path.resolve()` + `relative_to(root)` 证明真实路径仍在 root 内。`store_upload()`：

1. 验证 `file_key` 为 `file_<uuid4 hex>`；
2. 在 `uploads/<file_key>.tmp-<uuid>` 下以固定大小块读取流，累计大小并同步 SHA256；
3. 超过上限时删除临时目录并抛出 `UploadTooLargeError`；
4. 写入文件名 `payload`，再使用 `os.replace()` 原子移动到 `uploads/<file_key>/`；
5. 返回相对路径、大小和小写 SHA256。

`FileService` 在文件原子安装成功后再插入 `FileRecord` 并提交事务；如果数据库提交失败，删除已写文件。文件名使用 `Path(filename).name`，空名替换为 `upload.bin`；扩展名规范化为小写（含点）或 `None`。未找到或状态非 `AVAILABLE` 时抛出 `HubError(code="FILE_NOT_FOUND", message=f"文件 {file_key} 不存在", status_code=404)`。

- [ ] **步骤 4：运行验证**

运行：

```text
python -m pytest tests/server/test_storage.py -v
python -m ruff check packages/hub-server tests/server
python -m mypy packages/hub-server/src
```

预期：全部通过。

- [ ] **步骤 5：提交存储服务**

```bash
git add packages/hub-server tests/server
git commit -m "feat(server): add safe local upload storage"
```

## 任务 4：File REST API 与统一错误响应

**文件：**

- 新建：`packages/hub-server/src/hub_server/dependencies.py`
- 新建：`packages/hub-server/src/hub_server/routers/files.py`
- 修改：`packages/hub-server/src/hub_server/schemas.py`
- 修改：`packages/hub-server/src/hub_server/errors.py`
- 修改：`packages/hub-server/src/hub_server/main.py`
- 新建：`tests/server/test_files_api.py`

**接口：**

- 输入：multipart `file` 字段。
- 输出：`POST /api/v1/files` 返回 201 `FileResponse`；`GET /api/v1/files/{file_key}`；`GET /api/v1/files/{file_key}/download`；统一 `ErrorResponse`。

- [ ] **步骤 1：编写 API 失败和成功测试**

```python
def test_upload_then_get_metadata_and_download(client: TestClient) -> None:
    created = client.post(
        "/api/v1/files",
        files={"file": ("model.NC", b"netcdf-data", "application/x-netcdf")},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "model.NC"
    assert body["extension"] == ".nc"
    file_key = body["file_id"]
    assert client.get(f"/api/v1/files/{file_key}").json()["sha256"] == body["sha256"]
    assert client.get(f"/api/v1/files/{file_key}/download").content == b"netcdf-data"


def test_missing_file_uses_stable_error_shape(client: TestClient) -> None:
    response = client.get("/api/v1/files/file_missing")
    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "error": {"code": "FILE_NOT_FOUND", "message": "文件 file_missing 不存在", "details": None},
    }


def test_rejects_upload_larger_than_limit(client: TestClient) -> None:
    response = client.post("/api/v1/files", files={"file": ("a.nc", b"01234567890")})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/server/test_files_api.py -v`

预期：因 File router 和错误处理缺失而失败。

- [ ] **步骤 3：实现 schemas、依赖和 routers**

定义：

```python
class FileResponse(BaseModel):
    file_id: str
    name: str
    size: int
    sha256: str
    extension: str | None
    mime_type: str | None
    status: Literal["AVAILABLE"]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, object] | None = None


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    error: ErrorBody
```

`dependencies.py` 从 `request.app.state` 获取 SessionFactory、Storage 和 Settings，每请求产生一个 Session 并在 finally 关闭。`POST` 使用 `UploadFile.file` 调用 FileService；`GET download` 使用 `FileResponse`（Starlette 的 `FileResponse`，导入时别名为 `StreamingFileResponse`）并设置安全 `Content-Disposition`。`HubError` 异常处理器始终返回 ErrorResponse；未处理异常记录日志并返回 `500 UNEXPECTED_ERROR`，不返回 traceback 或绝对路径。

- [ ] **步骤 4：运行验证**

运行：

```text
python -m pytest tests/server/test_files_api.py tests/server/test_system_api.py -v
python -m ruff check packages/hub-server tests/server
python -m mypy packages/hub-server/src
```

预期：全部通过。

- [ ] **步骤 5：提交 API**

```bash
git add packages/hub-server tests/server
git commit -m "feat(server): add file upload api"
```

## 任务 5：AMD64 Docker Compose 与 Nginx 部署验收

**文件：**

- 新建：`Dockerfile`
- 新建：`compose.yaml`
- 新建：`config/hub.yaml.example`
- 新建：`deploy/nginx/python-service-hub.conf.example`
- 修改：`README.md`
- 新建：`tests/server/test_container_smoke.py`

**接口：**

- 输入：`docker compose up -d` 和 `/srv/python-service-hub/data` 持久卷。
- 输出：本机 `127.0.0.1:8000` 的健康检查与可持久化文件 API。

- [ ] **步骤 1：编写容器 smoke 测试**

```python
@pytest.mark.integration
def test_compose_health_and_upload() -> None:
    subprocess.run(["docker", "compose", "up", "-d", "--build"], check=True)
    assert httpx.get("http://127.0.0.1:8000/api/v1/system/health", timeout=30).json() == {"status": "UP"}
    response = httpx.post(
        "http://127.0.0.1:8000/api/v1/files",
        files={"file": ("smoke.nc", b"data")},
        timeout=30,
    )
    assert response.status_code == 201
```

测试使用 `pytest.mark.integration`，默认测试套件不执行；它只在 Linux AMD64 Docker 主机运行。

- [ ] **步骤 2：编写 Dockerfile 与 Compose**

Dockerfile 使用 `python:3.12-slim` 的 AMD64 兼容镜像，创建非 root `hub` 用户，安装 server wheel/依赖，不安装 GDAL 或任何插件业务依赖，以 `uvicorn hub_server.main:app --host 0.0.0.0 --port 8000` 启动。Compose 必须仅发布 `127.0.0.1:8000:8000`，只读挂载配置文件、读写挂载 `./data:/data`，并设置 `restart: unless-stopped`。

示例配置：

```yaml
deployment:
  mode: offline
storage:
  root: /data
database:
  url: sqlite:////data/db/hub.db
uploads:
  max_size_bytes: 10737418240
```

Nginx 示例监听 443，使用 `server_name hub.internal.example`、`ssl_certificate /etc/nginx/certs/hub.crt`、`ssl_certificate_key /etc/nginx/certs/hub.key`，并配置 `client_max_body_size 10g`、`proxy_read_timeout 3700s`、`proxy_send_timeout 3700s`、`proxy_pass http://127.0.0.1:8000`。部署时运维人员应替换域名和证书文件，并按实际内网网段加入 `allow`/`deny` 指令。

- [ ] **步骤 3：更新运行说明**

README 写明 Linux AMD64 部署命令：

```bash
sudo install -d -m 0750 /srv/python-service-hub/{config,data}
sudo cp compose.yaml /srv/python-service-hub/compose.yaml
sudo cp config/hub.yaml.example /srv/python-service-hub/config/hub.yaml
cd /srv/python-service-hub
sudo docker compose up -d --build
curl http://127.0.0.1:8000/api/v1/system/health
```

明确此阶段只提供系统/文件 API，插件安装和 Job 执行属于后续计划。

- [ ] **步骤 4：运行完整验证**

运行：

```text
python -m pytest
python -m ruff check .
python -m mypy packages
docker compose config
```

在 Linux AMD64 Docker 主机额外运行：

```text
python -m pytest -m integration tests/server/test_container_smoke.py -v
docker compose down --volumes
```

预期：全部通过。容器 smoke 后删除测试用卷，不得删除真实 `/srv/python-service-hub/data`。

- [ ] **步骤 5：提交可部署基础服务**

```bash
git add Dockerfile compose.yaml config deploy README.md packages tests pyproject.toml
git commit -m "feat(deploy): ship amd64 hub foundation"
```

## 阶段验收

- Linux AMD64 上 `docker compose up -d --build` 后，`GET /api/v1/system/health` 返回 `{"status":"UP"}`。
- Hub 仅发布给 `127.0.0.1:8000`；Nginx 示例负责 HTTPS、来源限制、10 GB 上传和长超时。
- 上传 NC 文件后，可以读取元数据、下载内容；重启 Compose 后数据库和上传文件仍可用。
- 文件超限、未知 File、路径逃逸和未预期异常返回结构化响应，且不泄露 host 路径或 traceback。
- pytest、Ruff、严格 mypy、`docker compose config` 通过；Linux AMD64 容器 smoke 通过。
- 不实现 Plugin 安装、Environment 解包、Job API、Runner 或子进程执行。
