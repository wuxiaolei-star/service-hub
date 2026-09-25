#!/usr/bin/env bash
# Build both runtime images from a checked-out source tree and tag them.
#
# Replaces the host-local /opt/service-hub/build.sh so the build recipe ships with
# the code: the pipeline calls this script from the freshly checked-out tree.
#
# Usage: build-images.sh <commit-sha>
#
# Tags produced per image:
#   <repo>:1.0.0-<sha>        immutable, names exactly what this commit built
#   <repo>:1.0.0-linux-amd64  moving tag consumed by compose.yaml and the gate
#
# Optional environment:
#   PIP_INDEX_URL / PIP_MIRROR  PyPI mirror for the backend image
#   APT_MIRROR                  Debian archive mirror for the backend image
#   NPM_REGISTRY / NPM_MIRROR   npm registry for the web image
set -euo pipefail

SRC_DIR="${SRC_DIR:-/opt/service-hub/src}"
API_IMAGE="python-service-hub"
WEB_IMAGE="python-service-hub-web"
VERSION_TAG="1.0.0-linux-amd64"
stamp() { date -Is; }

SHA="${1:?commit sha required: build-images.sh <sha>}"
# The host reaches regional mirrors far faster than the default indexes, and the
# Dockerfiles take all three as optional build args (empty keeps the default).
PIP_INDEX_URL="${PIP_INDEX_URL:-${PIP_MIRROR:-https://mirrors.cloud.tencent.com/pypi/simple/}}"
APT_MIRROR="${APT_MIRROR:-https://mirrors.cloud.tencent.com}"
NPM_REGISTRY="${NPM_REGISTRY:-${NPM_MIRROR:-https://registry.npmmirror.com}}"

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }

cd "$SRC_DIR"
echo "[$(stamp)] === base images (must be local) ==="
for i in python:3.12-slim node:22-alpine nginx:1.27-alpine; do
  docker image inspect "$i" --format "{{.RepoTags}} {{.Id}}" \
    || { echo "MISSING $i; pull it once over a fast link" >&2; exit 1; }
done

echo "[$(stamp)] === build backend image ($API_IMAGE:$VERSION_TAG-$SHA) ==="
# Requires BuildKit (docker 23+ enables it by default) for the RUN --mount cache
# mounts in the Dockerfiles.
DOCKER_BUILDKIT=1 docker build --progress=plain \
  --build-arg APT_MIRROR="$APT_MIRROR" \
  --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" \
  -t "$API_IMAGE:$VERSION_TAG-$SHA" .
docker tag "$API_IMAGE:$VERSION_TAG-$SHA" "$API_IMAGE:$VERSION_TAG"

echo "[$(stamp)] === build web image ($WEB_IMAGE:$VERSION_TAG-$SHA) ==="
DOCKER_BUILDKIT=1 docker build --progress=plain \
  --build-arg NPM_REGISTRY="$NPM_REGISTRY" \
  -t "$WEB_IMAGE:$VERSION_TAG-$SHA" ./web
docker tag "$WEB_IMAGE:$VERSION_TAG-$SHA" "$WEB_IMAGE:$VERSION_TAG"

echo "[$(stamp)] === BUILD FINISHED ==="
docker images --format '{{.Repository}}:{{.Tag}} id={{.ID}} size={{.Size}}' \
  | grep -E '^python-service-hub(-web)?'
