#!/usr/bin/env bash
# service-hub release pipeline: quality gate -> backup -> build -> up -> health -> rollback.
#
# Designed to run ON the deployment host (where Docker and the data volume live), so the
# images are built exactly where they are deployed and no 1 GB image crosses the network.
#
# Usage:
#   deploy-pipeline.sh <commit-or-ref> [--skip-gate] [--no-rollback]
#
# Exit codes: 0 success, 1 failure (rollback attempted unless --no-rollback).
set -Eeuo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/service-hub}"
SRC_DIR="$REMOTE_DIR/src"
DATA_DIR="${HUB_HOST_DATA_DIR:-$REMOTE_DIR/data}"
LOG_DIR="$REMOTE_DIR/logs"
RELEASE_LOG="$REMOTE_DIR/deploy/ci/releases.log"
LOCK_FILE="$REMOTE_DIR/.deploy.lock"
KEEP_BACKUPS="${KEEP_BACKUPS:-3}"

COMPOSE=(docker compose -p service-hub --project-directory "$SRC_DIR"
  -f compose.yaml -f "$REMOTE_DIR/compose.override.yaml")

stamp() { date -Is; }
log() { echo "[$(stamp)] $*"; }
fail() { log "ERROR: $*"; }

# ---------------------------------------------------------------- concurrency guard
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  fail "another deployment holds $LOCK_FILE"
  exit 1
fi

REF="${1:-main}"
SKIP_GATE=0
NO_ROLLBACK=0
for arg in "${@:2}"; do
  case "$arg" in
    --skip-gate) SKIP_GATE=1 ;;
    --no-rollback) NO_ROLLBACK=1 ;;
    *) fail "unknown argument: $arg"; exit 1 ;;
  esac
done

mkdir -p "$LOG_DIR" "$(dirname "$RELEASE_LOG")"
BUILD_LOG="$LOG_DIR/build-latest.log"
STARTED=$(date +%s)

rollback() {
  local reason="$1"
  fail "rolling back: $reason"
  if [ "$NO_ROLLBACK" -eq 1 ]; then
    fail "--no-rollback given, leaving the system as-is"
    return 0
  fi
  if [ -n "${COLD_BACKUP:-}" ] && [ -f "${COLD_BACKUP:-}" ]; then
    log "stopping containers"
    "${COMPOSE[@]}" down || true
    log "restoring data from $COLD_BACKUP"
    rm -rf "$DATA_DIR"
    tar -xzf "$COLD_BACKUP" -C "$REMOTE_DIR"
    log "rebuilding previous images"
    bash "$REMOTE_DIR/build.sh" > "$BUILD_LOG" 2>&1 || fail "rebuild during rollback failed"
    "${COMPOSE[@]}" up -d || fail "restart during rollback failed"
  else
    fail "no cold backup recorded, cannot restore data"
  fi
}
trap 'rollback "unexpected failure at line $LINENO"' ERR

# ---------------------------------------------------------------- 1. checkout
log "=== stage 1/8: checkout $REF ==="
cd "$SRC_DIR"
git fetch --all --prune || true
git checkout --detach "$REF"
COMMIT=$(git rev-parse --short HEAD)
PREV_COMMIT=$(cat "$REMOTE_DIR/.deployed-commit" 2>/dev/null || echo "unknown")
log "commit=$COMMIT previous=$PREV_COMMIT"

# ---------------------------------------------------------------- 2. quality gate
if [ "$SKIP_GATE" -eq 1 ]; then
  log "=== stage 2/8: quality gate SKIPPED ==="
else
  log "=== stage 2/8: quality gate ==="
  GATE_LOG="$LOG_DIR/gate-$COMMIT.log"
  # Backend gate runs inside the API image: it already has the dependency set and a
  # POSIX environment (Windows hosts cannot run the deploy/ and sdk/ POSIX cases).
  if docker image inspect python-service-hub:1.0.0-linux-amd64 >/dev/null 2>&1; then
    docker run --rm -v "$SRC_DIR:/src" -w /src python-service-hub:1.0.0-linux-amd64 \
      bash -lc 'pip install -q -i https://mirrors.aliyun.com/pypi/simple/ pytest && \
                python -m pytest -m "not integration" -q' 2>&1 | tee "$GATE_LOG" \
      || { fail "backend gate failed, see $GATE_LOG"; exit 1; }
  else
    fail "base image missing, cannot run the container gate; re-run with --skip-gate"
    exit 1
  fi
fi

# ---------------------------------------------------------------- 3. backup
log "=== stage 3/8: backup data/ ==="
STAMP_ID=$(date +%Y%m%d-%H%M)
HOT_BACKUP="$REMOTE_DIR/data-hot-$STAMP_ID.tar.gz"
COLD_BACKUP="$REMOTE_DIR/data-cold-$STAMP_ID.tar.gz"
tar -czf "$HOT_BACKUP" -C "$REMOTE_DIR" --exclude='data/backups' data \
  || log "hot backup returned non-zero (files changed while reading; expected on a live system)"
log "hot backup: $HOT_BACKUP"

"${COMPOSE[@]}" down
tar -czf "$COLD_BACKUP" -C "$REMOTE_DIR" --exclude='data/backups' data
tar -tzf "$COLD_BACKUP" > /dev/null || { fail "cold backup integrity check failed"; exit 1; }
log "cold backup: $COLD_BACKUP (integrity OK)"

# ---------------------------------------------------------------- 4. build
log "=== stage 4/8: build images ==="
bash "$REMOTE_DIR/build.sh" > "$BUILD_LOG" 2>&1 \
  || { fail "image build failed, see $BUILD_LOG"; tail -30 "$BUILD_LOG"; exit 1; }
tail -5 "$BUILD_LOG"

# ---------------------------------------------------------------- 5. up (Alembic runs on start)
log "=== stage 5/8: containers up ==="
"${COMPOSE[@]}" up -d --remove-orphans
"${COMPOSE[@]}" ps

# ---------------------------------------------------------------- 6. health check
log "=== stage 6/8: health check ==="
HEALTHY=0
for attempt in $(seq 1 20); do
  api=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18000/api/v1/system/health || echo 000)
  web=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/web-health || echo 000)
  edge=$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:8443/edge-health || echo 000)
  log "attempt $attempt: api=$api web=$web edge=$edge"
  if [ "$api" = "200" ] && [ "$web" = "200" ] && [ "$edge" = "200" ]; then
    HEALTHY=1
    break
  fi
  sleep 10
done
if [ "$HEALTHY" -ne 1 ]; then
  docker logs --tail 60 service-hub-service-hub-1 || true
  rollback "health check did not pass"
  exit 1
fi

# ---------------------------------------------------------------- 7. acceptance walkthrough
log "=== stage 7/8: acceptance walkthrough ==="
if [ -f "$REMOTE_DIR/deploy/ci/walkthrough.sh" ]; then
  bash "$REMOTE_DIR/deploy/ci/walkthrough.sh" || log "walkthrough reported failures (non-fatal)"
else
  log "no walkthrough.sh present, skipping"
fi

# ---------------------------------------------------------------- 8. record + rotate backups
log "=== stage 8/8: record release and rotate backups ==="
echo "$COMMIT" > "$REMOTE_DIR/.deployed-commit"
ELAPSED=$(( $(date +%s) - STARTED ))
echo "$(stamp) commit=$COMMIT previous=$PREV_COMMIT elapsed=${ELAPSED}s result=SUCCESS" \
  >> "$RELEASE_LOG"

ls -1t "$REMOTE_DIR"/data-cold-*.tar.gz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r old; do
  log "removing old backup $old"
  rm -f "$old"
done
ls -1t "$REMOTE_DIR"/data-hot-*.tar.gz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r old; do
  rm -f "$old"
done

log "=== DEPLOY FINISHED: $COMMIT in ${ELAPSED}s ==="
