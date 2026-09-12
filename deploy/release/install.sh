#!/usr/bin/env bash
set -euo pipefail

DEFAULT_DATA_DIR="/srv/service-hub-data"

fail() {
    echo "install.sh: $*" >&2
    exit 1
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

read_env_data_dir() {
    python3 - <<'PY'
from pathlib import Path

for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("HUB_HOST_DATA_DIR="):
        print(line.split("=", 1)[1])
        break
PY
}

cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"
docker compose version >/dev/null 2>&1 || fail "docker compose is required"

sha256sum -c SHA256SUMS

if [ -f ".env" ]; then
    data_dir="$(read_env_data_dir)"
    [ -n "${data_dir}" ] || fail ".env does not contain HUB_HOST_DATA_DIR"
else
    data_dir="${HUB_HOST_DATA_DIR:-${DEFAULT_DATA_DIR}}"
fi
data_dir="$(validate_data_dir "${data_dir}")"

install -d -m 0750 "${data_dir}"

docker load -i service-hub-image.tar

if [ ! -f ".env" ]; then
    umask 077
    printf 'HUB_HOST_DATA_DIR=%s\n' "${data_dir}" > ".env"
fi

echo "Installed Service Hub release"
echo "HUB_HOST_DATA_DIR=${data_dir}"
