#!/usr/bin/env bash
# service-hub release pipeline: quality gate -> backup -> build -> up -> health -> rollback.
#
# Designed to run ON the deployment host (where Docker and the data volume live), so the
# images are built exactly where they are deployed and no 1 GB image crosses the network.
#
# Usage:
#   deploy-pipeline.sh <commit-or-ref> [--skip-gate] [--run-gate] [--no-rollback]
#
# The server-side quality gate is OFF by default: GitHub Actions already ran the full
# backend/frontend gates before the commit reached main. Set HUB_PIPELINE_GATE=1 in the
# environment (or pass --run-gate) to run the in-container pytest gate here anyway;
# --skip-gate forces it off regardless. Without either flag the gate decision is
# HUB_PIPELINE_GATE, so a bare `HUB_PIPELINE_GATE=1 deploy-pipeline.sh <ref>` works.
#
# Images are tagged per commit (python-service-hub:1.0.0-<sha>) and the moving
# 1.0.0-linux-amd64 tag that compose.yaml consumes is pointed at the new image.
# The image that was live before the build is kept as *:rollback-target, so a
# failed health check rolls back by re-pointing tags instead of rebuilding.
#
# Optional environment:
#   HUB_DEPLOY_WEBHOOK_URL   POSTed a JSON summary on success and on failure
#                            (non-fatal, 5 s timeout; point it at a bot webhook)
#   HUB_MIN_FREE_MB          refuse to deploy below this free-MB watermark (default 2048)
#
# Exit codes: 0 success, 1 failure (rollback attempted unless --no-rollback),
#             3 another deployment holds the lock (benign for the scheduled poller).
set -Eeuo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/service-hub}"
SRC_DIR="$REMOTE_DIR/src"
DATA_DIR="${HUB_HOST_DATA_DIR:-$REMOTE_DIR/data}"
LOG_DIR="$REMOTE_DIR/logs"
RELEASE_LOG="$REMOTE_DIR/deploy/ci/releases.log"
LOCK_FILE="$REMOTE_DIR/.deploy.lock"
KEEP_BACKUPS="${KEEP_BACKUPS:-3}"
API_IMAGE="python-service-hub"
WEB_IMAGE="python-service-hub-web"
VERSION_TAG="1.0.0-linux-amd64"
ROLLBACK_TAG="rollback-target"

COMPOSE=(docker compose -p service-hub --project-directory "$SRC_DIR"
  -f compose.yaml -f "$REMOTE_DIR/compose.override.yaml")

# compose.yaml interpolates ${HUB_HOST_DATA_DIR:?...} for the API service's
# environment and volumes. Without it every compose call in this pipeline fails
# ("required variable HUB_HOST_DATA_DIR is missing a value"), which under `set -e`
# looks like a broken backup rather than a missing environment variable.
export HUB_HOST_DATA_DIR="$DATA_DIR"

stamp() { date -Is; }
log() { echo "[$(stamp)] $*"; }
fail() { log "ERROR: $*"; }

NOTIFIED=0
notify() {
  [ "$NOTIFIED" -eq 0 ] || return 0
  NOTIFIED=1
  local result="$1" commit="${2:-unknown}" message="$3"
  if [ -z "${HUB_DEPLOY_WEBHOOK_URL:-}" ]; then
    return 0
  fi
  # Generic JSON body: most bot webhooks (WeCom/DingTalk/Slack-compatible relays)
  # accept at least the text field, and a failed notification must never fail a
  # deployment that otherwise succeeded.
  curl -fsS -m 5 -X POST -H 'Content-Type: application/json' \
    -d "{\"text\":\"service-hub deploy $result commit=$commit: $message\"}" \
    "$HUB_DEPLOY_WEBHOOK_URL" >/dev/null \
    || fail "webhook notification failed (non-fatal)"
  return 0
}

# ---------------------------------------------------------------- concurrency guard
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  # Exit 3, not 1: a deployment already running is a normal condition for a
  # scheduled poller, and pull-deploy.sh turns it into a clean no-op. Reporting
  # it as a failure would make every overlapping timer tick look like an outage.
  fail "another deployment holds $LOCK_FILE"
  exit 3
fi

REF="${1:-main}"
SKIP_GATE=""
NO_ROLLBACK=0
for arg in "${@:2}"; do
  case "$arg" in
    --skip-gate) SKIP_GATE=1 ;;
    --run-gate) SKIP_GATE=0 ;;
    --no-rollback) NO_ROLLBACK=1 ;;
    *) fail "unknown argument: $arg"; exit 1 ;;
  esac
done
if [ -z "$SKIP_GATE" ]; then
  case "${HUB_PIPELINE_GATE:-0}" in
    1 | true | yes | run) SKIP_GATE=0 ;;
    *) SKIP_GATE=1 ;;
  esac
fi

mkdir -p "$LOG_DIR" "$(dirname "$RELEASE_LOG")"
BUILD_LOG="$LOG_DIR/build-latest.log"
STARTED=$(date +%s)

# Images live before this build, recorded as IDs. They are re-tagged to
# *:$ROLLBACK_TAG only after the new build succeeded, so a broken build leaves
# the previous state untouched.
PREV_API_ID=""
PREV_WEB_ID=""
record_current_images() {
  PREV_API_ID=$(docker image inspect --format '{{.Id}}' "$API_IMAGE:$VERSION_TAG" 2>/dev/null || echo "")
  PREV_WEB_ID=$(docker image inspect --format '{{.Id}}' "$WEB_IMAGE:$VERSION_TAG" 2>/dev/null || echo "")
}

point_moving_tags_at_rollback() {
  docker tag "$PREV_API_ID" "$API_IMAGE:$ROLLBACK_TAG"
  docker tag "$PREV_WEB_ID" "$WEB_IMAGE:$ROLLBACK_TAG"
}

wait_healthy() {
  local attempt api web edge
  for attempt in $(seq 1 12); do
    api=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18000/api/v1/system/health || echo 000)
    web=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/web-health || echo 000)
    edge=$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:8443/edge-health || echo 000)
    log "attempt $attempt: api=$api web=$web edge=$edge"
    if [ "$api" = "200" ] && [ "$web" = "200" ] && [ "$edge" = "200" ]; then
      return 0
    fi
    sleep 10
  done
  return 1
}

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
    if [ -n "$PREV_API_ID" ] && docker image inspect "$API_IMAGE:$ROLLBACK_TAG" >/dev/null 2>&1; then
      # Roll back code in seconds by re-pointing the moving tags at the last
      # known-good images instead of rebuilding from source. A rebuild from the
      # checked-out tree would put the *failed* commit's code back into service,
      # which is exactly what a rollback must not do.
      log "restoring images from $ROLLBACK_TAG tags"
      docker tag "$API_IMAGE:$ROLLBACK_TAG" "$API_IMAGE:$VERSION_TAG"
      docker tag "$WEB_IMAGE:$ROLLBACK_TAG" "$WEB_IMAGE:$VERSION_TAG"
    else
      # First deployment: there is no previous image to fall back to, so rebuild
      # from the checked-out tree as the only available option.
      fail "no $ROLLBACK_TAG image recorded; rebuilding from source (best effort)"
      bash "$REMOTE_DIR/build.sh" > "$BUILD_LOG" 2>&1 || fail "rebuild during rollback failed"
    fi
    "${COMPOSE[@]}" up -d || fail "restart during rollback failed"
  else
    fail "no cold backup recorded, cannot restore data"
  fi
}
on_error() {
  rollback "unexpected failure at line $1"
  notify FAILED "${COMMIT:-unknown}" "pipeline failed at line $1 and rolled back"
}
trap 'on_error $LINENO' ERR

# ---------------------------------------------------------------- 1. checkout
log "=== stage 1/8: checkout $REF ==="
cd "$SRC_DIR"
git fetch --all --prune || true
git checkout --detach "$REF"
COMMIT=$(git rev-parse --short HEAD)
PREV_COMMIT=$(cat "$REMOTE_DIR/.deployed-commit" 2>/dev/null || echo "unknown")
log "commit=$COMMIT previous=$PREV_COMMIT"

# ---------------------------------------------------------------- 2. disk watermark
AVAILABLE_MB=$(df -Pm "$REMOTE_DIR" | awk 'NR==2 {print $4}')
MIN_FREE_MB="${HUB_MIN_FREE_MB:-2048}"
log "=== stage 2/8: disk watermark: ${AVAILABLE_MB}MB free (min ${MIN_FREE_MB}MB) ==="
if [ "$AVAILABLE_MB" -lt "$MIN_FREE_MB" ]; then
  fail "only ${AVAILABLE_MB}MB free on $REMOTE_DIR (minimum ${MIN_FREE_MB}MB); refusing to deploy"
  notify FAILED "$COMMIT" "disk watermark: only ${AVAILABLE_MB}MB free"
  exit 1
fi

# ---------------------------------------------------------------- 3. quality gate
if [ "$SKIP_GATE" -eq 1 ]; then
  log "=== stage 3/8: quality gate SKIPPED (owned by GitHub Actions) ==="
else
  log "=== stage 3/8: quality gate ==="
  GATE_LOG="$LOG_DIR/gate-$COMMIT.log"
  # Backend gate runs inside the API image: it already has the dependency set and a
  # POSIX environment (Windows hosts cannot run the deploy/ and sdk/ POSIX cases).
  # --entrypoint bash is mandatory: the image entrypoint (service-hub-entrypoint)
  # bootstraps the data directory first and exits with KeyError: HUB_HOST_DATA_DIR
  # before ever reaching pytest, which looks exactly like a failing gate.
  if docker image inspect "$API_IMAGE:$VERSION_TAG" >/dev/null 2>&1; then
    docker run --rm --entrypoint bash -v "$SRC_DIR:/src" -w /src "$API_IMAGE:$VERSION_TAG" \
      -c 'pip install -q -i https://mirrors.tencent.com/pypi/simple/ pytest && \
          python -m pytest -m "not integration" -q' 2>&1 | tee "$GATE_LOG" \
      || { fail "backend gate failed, see $GATE_LOG"; exit 1; }
  else
    fail "base image missing, cannot run the container gate; re-run with --skip-gate"
    exit 1
  fi
fi

# ---------------------------------------------------------------- 4. backup
log "=== stage 4/8: backup data/ ==="
STAMP_ID=$(date +%Y%m%d-%H%M)
HOT_BACKUP="$REMOTE_DIR/data-hot-$STAMP_ID.tar.gz"
COLD_BACKUP="$REMOTE_DIR/data-cold-$STAMP_ID.tar.gz"
tar -czf "$HOT_BACKUP" -C "$REMOTE_DIR" --exclude='data/backups' data \
  || log "hot backup returned non-zero (files changed while reading; expected on a live system)"
log "hot backup: $HOT_BACKUP"

record_current_images
"${COMPOSE[@]}" down
tar -czf "$COLD_BACKUP" -C "$REMOTE_DIR" --exclude='data/backups' data
tar -tzf "$COLD_BACKUP" > /dev/null || { fail "cold backup integrity check failed"; exit 1; }
log "cold backup: $COLD_BACKUP (integrity OK)"

# ---------------------------------------------------------------- 5. build
log "=== stage 5/8: build images (commit tag $VERSION_TAG-$COMMIT) ==="
# Prefer the freshly checked-out build script so build improvements ship with the
# code that uses them; fall back to the host copy for older checkouts.
if [ -f "$SRC_DIR/deploy/ci/build-images.sh" ]; then
  SRC_DIR="$SRC_DIR" bash "$SRC_DIR/deploy/ci/build-images.sh" "$COMMIT" > "$BUILD_LOG" 2>&1 \
    || { fail "image build failed, see $BUILD_LOG"; tail -30 "$BUILD_LOG"; exit 1; }
else
  bash "$REMOTE_DIR/build.sh" > "$BUILD_LOG" 2>&1 \
    || { fail "image build failed, see $BUILD_LOG"; tail -30 "$BUILD_LOG"; exit 1; }
fi
tail -5 "$BUILD_LOG"
if [ -n "$PREV_API_ID" ]; then
  point_moving_tags_at_rollback
  log "previous images kept as $ROLLBACK_TAG tags (api=$PREV_API_ID web=$PREV_WEB_ID)"
fi

# ---------------------------------------------------------------- 6. up (Alembic runs on start)
log "=== stage 6/8: containers up ==="
"${COMPOSE[@]}" up -d --remove-orphans
"${COMPOSE[@]}" ps

# ---------------------------------------------------------------- 7. health check
log "=== stage 7/8: health check ==="
if ! wait_healthy; then
  docker logs --tail 60 service-hub-service-hub-1 || true
  rollback "health check did not pass"
  notify FAILED "$COMMIT" "health check failed and rolled back"
  exit 1
fi

# ---------------------------------------------------------------- 8. acceptance walkthrough
log "=== stage 8/8: acceptance walkthrough ==="
if [ -f "$SRC_DIR/deploy/ci/walkthrough.sh" ]; then
  bash "$SRC_DIR/deploy/ci/walkthrough.sh" || log "walkthrough reported failures (non-fatal)"
elif [ -f "$REMOTE_DIR/deploy/ci/walkthrough.sh" ]; then
  bash "$REMOTE_DIR/deploy/ci/walkthrough.sh" || log "walkthrough reported failures (non-fatal)"
else
  log "no walkthrough.sh present, skipping"
fi

# ---------------------------------------------------------------- record + rotate
echo "$COMMIT" > "$REMOTE_DIR/.deployed-commit"
ELAPSED=$(( $(date +%s) - STARTED ))
echo "$(stamp) commit=$COMMIT previous=$PREV_COMMIT elapsed=${ELAPSED}s result=SUCCESS" \
  >> "$RELEASE_LOG"
log "=== DEPLOY FINISHED: $COMMIT in ${ELAPSED}s ==="
notify SUCCESS "$COMMIT" "deploy finished in ${ELAPSED}s"

# Drop superseded per-commit tags so images do not accumulate on a small disk;
# the moving tag, the rollback target and the just-built commit tag all survive.
KEEP_TAGS=("$API_IMAGE:$VERSION_TAG-$COMMIT" "$WEB_IMAGE:$VERSION_TAG-$COMMIT")
for repo in "$API_IMAGE" "$WEB_IMAGE"; do
  docker images --format '{{.Repository}}:{{.Tag}}' "$repo" | while read -r ref; do
    keep=0
    for tag in *:"$VERSION_TAG" "$repo:$ROLLBACK_TAG" "${KEEP_TAGS[@]}"; do
      [ "$ref" = "$tag" ] && keep=1
    done
    if [ "$keep" -eq 0 ]; then
      case "$ref" in
        $repo:$VERSION_TAG-*) docker rmi "$ref" >/dev/null 2>&1 || true ;;
      esac
    fi
  done
done
# NOTE: no `docker image prune` here, ever. The hub registry addresses plugin
# images by digest, and a re-tagged build leaves the attested image dangling —
# a prune then deletes it and every new job on that build fails instantly with
# "Runner failed" (observed 2026-09-25). Superseded main-image commit tags are
# removed explicitly above; every other docker object stays.

ls -1t "$REMOTE_DIR"/data-cold-*.tar.gz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r old; do
  log "removing old backup $old"
  rm -f "$old"
done
ls -1t "$REMOTE_DIR"/data-hot-*.tar.gz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r old; do
  rm -f "$old"
done
