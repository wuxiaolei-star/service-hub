# Task 6 Report — dependency-free hubctl

## Scope

Implemented only Task 6:

- Added `tools/hubctl`.
- Added focused tests in `tests/tools/test_hubctl.py`.
- Did not modify server endpoints, deployment scripts, publisher/templates, Compose, image code, or docs.

## Contract Alignment

The client follows the current implemented server routes and schemas:

- `GET /api/v1/system/health`
- `GET /api/v1/plugins`
- `POST /api/v1/plugins/install`
- `POST /api/v1/plugin-builds/{build_id}/enable`
- `POST /api/v1/files`
- `POST /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/{job_id}/logs`
- `POST /api/v1/jobs/{job_id}/cancel`
- `GET /api/v1/jobs/{job_id}/outputs`
- `GET /api/v1/files/{file_id}/download`

`job run` sends the current `JobCreateRequest` shape:

```json
{
  "plugin_id": "nc_to_shp",
  "version": "1.0.0",
  "runtime_type": "conda-pack",
  "inputs": {"source_nc": "file_123"},
  "params": {"group_name": "1"}
}
```

## TDD Evidence

RED:

```text
python -m pytest tests/tools/test_hubctl.py -q
14 errors, all failing with FileNotFoundError: tools/hubctl
```

GREEN:

```text
python -m pytest tests/tools/test_hubctl.py -q
14 passed in 0.30s
```

Covered behaviors:

- CLI command path and payload construction.
- `plugin install` and `file upload` use multipart upload paths.
- Hub error code/message are printed to stderr and return exit code 1.
- `job download` fetches outputs first, refuses overwrites, sanitizes unsafe names, and downloads through file download endpoints.
- HTTP multipart upload computes `Content-Length` and streams file content in bounded chunks through `HTTPConnection.send`.

## Verification

Passed:

```text
python -m pytest tests/tools/test_hubctl.py -q
.venv\Scripts\ruff.exe check tools/hubctl tests/tools/test_hubctl.py
.venv\Scripts\mypy.exe --strict tools/hubctl tests/tools/test_hubctl.py
python -m py_compile tools/hubctl tests/tools/test_hubctl.py
python tools/hubctl --help
python tools/hubctl job run --help
```

Notes:

- Global `python -m ruff ...` is unavailable because the active global Python does not have `ruff` installed, so the repository `.venv` Ruff was used.
- `pytest` exits 0, but this Windows host prints an unrelated `PermissionError` during pytest atexit cleanup for an old temp directory under `AppData\Local\Temp\pytest-of-Administrator\pytest-75`.

## Live Hub Smoke

Attempted:

```text
HUB_URL=http://127.0.0.1:8000 python tools/hubctl health
HUB_URL=http://127.0.0.1:8000 python tools/hubctl plugin list
```

Result: both exited 1 with `WinError 10061` because no Hub was listening on `127.0.0.1:8000` in this Windows worktree. No live Hub smoke pass is claimed.
