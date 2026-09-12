#!/usr/bin/env bash
set -euo pipefail

fail() {
    echo "start.sh: $*" >&2
    exit 1
}

read_env_data_dir() {
    python3 - <<'PY'
from pathlib import Path

for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("HUB_HOST_DATA_DIR="):
        print(line.split("=", 1)[1])
        break
PY
}

validate_data_dir() {
    python3 - "$1" <<'PY'
from pathlib import PurePosixPath
import sys

raw_path = sys.argv[1]
if not raw_path.startswith("/"):
    print("HUB_HOST_DATA_DIR must be an absolute POSIX path", file=sys.stderr)
    raise SystemExit(2)

parts: list[str] = []
for part in raw_path.split("/"):
    if part in {"", "."}:
        continue
    if part == "..":
        if parts:
            parts.pop()
        continue
    parts.append(part)

normalized_path = PurePosixPath("/" + "/".join(parts))
normalized = normalized_path.as_posix()
if normalized in {"/", "/srv", "/tmp", "/var", "/data"}:
    print(f"refusing unsafe HUB_HOST_DATA_DIR because it is too broad: {normalized}", file=sys.stderr)
    raise SystemExit(2)
if len(normalized_path.parts) < 3:
    print("HUB_HOST_DATA_DIR must have at least two path components", file=sys.stderr)
    raise SystemExit(2)
print(normalized)
PY
}

cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
[ -f ".env" ] || fail "run ./install.sh before ./start.sh"

data_dir="$(read_env_data_dir)"
[ -n "${data_dir}" ] || fail ".env does not contain HUB_HOST_DATA_DIR"
data_dir="$(validate_data_dir "${data_dir}")"
[ -d "${data_dir}" ] || fail "data directory does not exist: ${data_dir}"

export HUB_HOST_DATA_DIR="${data_dir}"
docker compose up -d
