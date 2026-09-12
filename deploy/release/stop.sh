#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || {
    echo "stop.sh: docker is required" >&2
    exit 1
}

docker compose stop
