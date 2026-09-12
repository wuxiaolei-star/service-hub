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
    value="$1"
    case "${value}" in
        /*) ;;
        *) fail "HUB_HOST_DATA_DIR must be an absolute path" ;;
    esac
    [ -d "${value}" ] || fail "data directory does not exist: ${value}"
}

cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
[ -f ".env" ] || fail "run ./install.sh before ./start.sh"

data_dir="$(read_env_data_dir)"
[ -n "${data_dir}" ] || fail ".env does not contain HUB_HOST_DATA_DIR"
validate_data_dir "${data_dir}"

docker compose up -d
