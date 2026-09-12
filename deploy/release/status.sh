#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || {
    echo "status.sh: docker is required" >&2
    exit 1
}
command -v python3 >/dev/null 2>&1 || {
    echo "status.sh: python3 is required" >&2
    exit 1
}

docker compose ps

container_id="$(docker compose ps -q service-hub || true)"
if [ -n "${container_id}" ]; then
    docker inspect \
        --format 'service-hub health={{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}} status={{.State.Status}}' \
        "${container_id}"
else
    echo "service-hub container is not created"
fi

python3 - <<'PY'
from http.client import HTTPConnection

try:
    connection = HTTPConnection("127.0.0.1", 8000, timeout=5)
    connection.request("GET", "/api/v1/system/health")
    response = connection.getresponse()
    body = response.read().decode("utf-8", errors="replace")
    print(f"hub health http_status={response.status} body={body}")
except Exception as error:
    print(f"hub health unavailable: {error}")
    raise SystemExit(1) from None
finally:
    try:
        connection.close()
    except NameError:
        pass
PY
