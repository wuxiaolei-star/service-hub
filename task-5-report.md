# Task 5 deployment report

## Delivered scope

- Added an architecture-neutral `python:3.12-slim` Dockerfile; Compose selects
  `linux/amd64` and tags the image `python-service-hub:0.1.0-linux-amd64`.
- The container migrates and serves the existing system/file APIs through
  Uvicorn. Its final server process runs as the non-root `hub` user. The
  entrypoint adjusts the writable bind-mounted data directory so the documented
  root-owned `/srv/python-service-hub/data` setup remains usable.
- Added Compose with a loopback-only `127.0.0.1:8000:8000` publication,
  read-only configuration and persistent `./data:/data` bind mount.
- Added the offline settings example and a checked-in default `config/hub.yaml`.
  The latter is necessary for the specified bare `docker compose up -d --build`
  smoke command to have a bind-mount source; deployment still copies and edits
  the example at `/srv/python-service-hub/config/hub.yaml`.
- Added an HTTPS Nginx example with private-network allow rules, access log,
  10 GB request limit and 3700-second proxy read/send timeouts.
- Added a marked integration smoke test for Compose health and file upload.
  The default pytest configuration excludes this marker.
- Updated the README with the requested Linux AMD64 commands, Nginx boundary,
  persistence guidance and current system/file-only scope.

No Plugin installation, Environment management, Job API, Runner, or subprocess
execution was added.

## Verification

| Command | Result |
| --- | --- |
| `.venv\\Scripts\\python.exe -m pytest` | 256 passed, 1 skipped, 1 integration test deselected |
| `.venv\\Scripts\\python.exe -m ruff check .` | Passed |
| `docker compose config` | Passed; confirms loopback port, AMD64 platform and mounts |
| `.venv\\Scripts\\python.exe -m pytest -m integration tests/server/test_container_smoke.py -v` | Blocked: Docker Desktop Linux daemon is not running (`//./pipe/dockerDesktopLinuxEngine` missing) |
| `.venv\\Scripts\\python.exe -m mypy packages` | Existing failures only: two unused `type: ignore` diagnostics in `packages/hub-contracts/src/python_hub_contracts/yaml_io.py` lines 7 and 14 |

The integration test was first run before the deployment files existed and failed
as expected because Compose could not find a configuration file. It was then
re-run after implementation; the current failure occurs before build/startup
because this Windows host has no available Docker Linux daemon. Run it on a
Linux AMD64 Docker host in a disposable checkout or directory as the release
gate; the Compose data bind mount is not removed by `docker compose down
--volumes`.
