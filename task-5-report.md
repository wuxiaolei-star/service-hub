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

## Review follow-up

- The source-build instructions now begin with a complete repository checkout
  in `/srv/python-service-hub` and name every file/directory required by the
  Compose build context. They no longer copy only Compose and configuration
  files before invoking `--build`.
- Production Compose retains its exact loopback port, `./data` and
  `./config/hub.yaml` declarations. The smoke test creates a complete temporary
  Compose file with its disposable port and data bind mount, so it never merges
  production port mappings into the test project.
- The smoke test now supplies a UUID-scoped Compose project, a temporary
  data directory and an ephemeral loopback port. Its `finally` block runs
  `down --volumes --remove-orphans` only for that project before
  `TemporaryDirectory` removes the exact directory it created.
- Removed the two stale PyYAML `type: ignore` comments. Strict mypy now passes
  for all 28 source files.

Fresh follow-up verification: the default test suite passed with 257 passed,
1 skipped and 1 integration test deselected; Ruff passed; `mypy packages`
passed; and `docker compose config` passed. The real Docker smoke remains an
external validation gap: this host has no Docker Desktop Linux daemon, so the
test cannot reach the image build/startup stage and was not represented as a
passing result.

## Second review follow-up: standalone smoke port mapping

The smoke test no longer passes production `compose.yaml` together with an
override. It writes a complete, temporary Compose definition containing the
same Docker build context and read-only Hub configuration, plus exactly one
random loopback port mapping and one temporary data bind mount. Therefore the
test cannot also bind `127.0.0.1:8000` or merge with a live deployment's
production port mapping. The UUID project name and guarded `finally` cleanup
are retained.

The real container smoke remains pending a Linux AMD64 Docker daemon; this
host's Docker Desktop Linux endpoint is unavailable before image build starts.

Fresh verification after this correction: 258 tests passed, 1 was skipped and
the 1 Docker integration test was deselected in the default suite; Ruff and
strict mypy passed; and production `docker compose config` passed. Running the
selected integration test again reached the isolated standalone Compose command
and stopped only because the Docker Desktop Linux daemon is unavailable.
