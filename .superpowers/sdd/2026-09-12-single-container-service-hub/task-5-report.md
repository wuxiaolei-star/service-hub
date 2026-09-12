# Task 5 deployment report

## Delivered scope

- Added `deploy/release/build-release.sh` for a repeatable Linux AMD64 offline
  bundle at `dist/service-hub-1.0.0-linux-amd64.tar.gz`.
- Added idempotent offline operator scripts:
  `install.sh`, `start.sh`, `stop.sh`, and `status.sh`.
- The release bundle copies the existing single-service `compose.yaml` and the
  existing Task 6 `tools/hubctl`; no production Compose behavior was changed.
- `install.sh` verifies `SHA256SUMS`, checks Docker, Docker Compose and
  `python3`, creates the absolute data directory with mode `0750`, loads
  `service-hub-image.tar`, and preserves an existing `.env` value.
- Lifecycle scripts avoid destructive teardown: `stop.sh` uses
  `docker compose stop`; no script uses `docker compose down -v`, `rm -rf`, or
  Docker Socket permission widening.
- Added `.gitattributes` for `deploy/release/*.sh` so the release shell scripts
  keep LF endings even on Windows checkouts.

I used `.superpowers/sdd/2026-09-12-single-container-service-hub/task-5-brief.md`,
the Task 5 section of
`docs/superpowers/plans/2026-09-12-single-container-service-hub.md`, and the
offline deployment sections of
`docs/superpowers/specs/2026-09-12-single-container-service-hub-design.md` as
the source of truth.

## TDD evidence

1. Added `tests/deploy/test_release_scripts.py` first.
2. Ran `python -m pytest tests/deploy/test_release_scripts.py -q`.
3. Confirmed RED: 4 failures because `deploy/release` and all Task 5 scripts
   were missing.
4. Implemented the scripts.
5. Confirmed GREEN with the same focused test.

## Verification

| Command | Result |
| --- | --- |
| `.\\.venv\\Scripts\\python.exe -m pytest tests/deploy/test_release_scripts.py tests/tools/test_hubctl.py -q` | Passed: 25 passed |
| `bash -n deploy/release/build-release.sh deploy/release/install.sh deploy/release/start.sh deploy/release/stop.sh deploy/release/status.sh` | Passed, exit 0; WSL printed a non-fatal localhost/NAT warning |
| `.\\.venv\\Scripts\\python.exe -m ruff check tests/deploy/test_release_scripts.py tools/hubctl tests/tools/test_hubctl.py` | Passed |
| `.\\.venv\\Scripts\\python.exe -m mypy deploy/service_hub` | Passed: no issues in 5 source files |
| `HUB_HOST_DATA_DIR=/srv/service-hub-data docker compose config --quiet` | Passed |
| `bash deploy/release/build-release.sh` | Blocked before Docker build: WSL reports `docker` is not installed in the distro |
| `docker version` | Blocked: Windows Docker CLI exists, but the Docker Desktop Linux daemon pipe is missing |
| `docker image inspect python-service-hub:1.0.0-linux-amd64` | Blocked for the same daemon reason |

Because this host cannot reach a Docker Linux daemon, the real image build,
`docker save`, final `dist/service-hub-1.0.0-linux-amd64.tar.gz` creation, and
`tar -tzf` content check were not completed here. The scripts and static release
contract were fully verified; the final bundle must be produced on a Linux AMD64
host with Docker available, or on this machine after Docker Desktop WSL
integration and the Linux daemon are running.

## Review fix round 1

- Added executable regression coverage for release data-directory handling and
  status health failures.
- `install.sh` and `start.sh` now perform POSIX lexical normalization equivalent
  to the Task 2 path rules without importing the deploy Python package.
- `install.sh` writes a normalized absolute `HUB_HOST_DATA_DIR` into new `.env`
  files and rejects broad normalized paths such as `/tmp/..` before data
  directory creation or image loading.
- `start.sh` revalidates the `.env` path, rejects broad `..` paths such as
  `/srv/foo/../..`, exports the normalized path for Compose, and only then
  starts the service.
- `status.sh` still prints compose/container status first; if Hub HTTP health is
  unavailable it prints one line beginning `hub health unavailable:` and returns
  nonzero without a Python traceback.

Fresh verification:

| Command | Result |
| --- | --- |
| `.\\.venv\\Scripts\\python.exe -m pytest tests/deploy/test_release_scripts.py tests/tools/test_hubctl.py -q` | Passed: 29 passed |
| `bash -n deploy/release/build-release.sh deploy/release/install.sh deploy/release/start.sh deploy/release/stop.sh deploy/release/status.sh` | Passed, exit 0; WSL printed a non-fatal localhost/NAT warning |
| `.\\.venv\\Scripts\\python.exe -m ruff check tests/deploy/test_release_scripts.py tools/hubctl tests/tools/test_hubctl.py` | Passed |
| `.\\.venv\\Scripts\\python.exe -m mypy deploy/service_hub` | Passed: no issues in 5 source files |
