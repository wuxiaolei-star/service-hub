#!/usr/bin/env bash
# Pull-based CD entry point for the deployment host.
#
# GitHub Actions owns the quality gate; this script owns the deployment. It asks
# the remote what the tip of the release branch is and, when it differs from the
# commit recorded in .deployed-commit, hands the sha to deploy-pipeline.sh.
# Nothing has to reach the server from the internet: the host pulls, so no SSH
# port or GitHub egress range needs to be opened.
#
# Usage:
#   pull-deploy.sh [--check] [--force] [--ref=<sha|tag>]
#
# Environment:
#   REMOTE_DIR      deployment root          (default /opt/service-hub)
#   DEPLOY_REMOTE   git remote name          (default origin)
#   DEPLOY_BRANCH   branch to follow         (default main)
#   DEPLOY_MODE     branch | tag             (default branch)
#   DEPLOY_TAG_GLOB tag pattern for tag mode (default 'v*')
#   PIPELINE_ARGS   extra flags for deploy-pipeline.sh, e.g. "--skip-gate"
#
# Exit codes: 0 nothing to do or deploy succeeded, 1 deploy failed,
#             2 another run already holds the lock (harmless when scheduled).
set -Eeuo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/service-hub}"
SRC_DIR="$REMOTE_DIR/src"
REMOTE="${DEPLOY_REMOTE:-origin}"
BRANCH="${DEPLOY_BRANCH:-main}"
MODE="${DEPLOY_MODE:-branch}"
TAG_GLOB="${DEPLOY_TAG_GLOB:-v*}"

STATE_FILE="$REMOTE_DIR/.deployed-commit"
LOCK_FILE="$REMOTE_DIR/.deploy-poll.lock"
LOG_DIR="$REMOTE_DIR/logs"

CHECK_ONLY=0
FORCE=0
EXPLICIT_REF=""

for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    --force) FORCE=1 ;;
    --ref=*) EXPLICIT_REF="${arg#--ref=}" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

stamp() { date -Is; }
log() { echo "[$(stamp)] $*"; }

mkdir -p "$LOG_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another deployment holds $LOCK_FILE, skipping this run"
  exit 2
fi

if [ ! -d "$SRC_DIR/.git" ]; then
  log "ERROR: $SRC_DIR is not a git checkout; run setup-deploy-key.sh first"
  exit 1
fi

cd "$SRC_DIR"
git fetch --quiet --prune "$REMOTE" 2>&1 | sed 's/^/  git: /'

if [ -n "$EXPLICIT_REF" ]; then
  TARGET="$EXPLICIT_REF"
elif [ "$MODE" = "tag" ]; then
  TARGET="$(git tag --list "$TAG_GLOB" --sort=-v:refname | head -n 1)"
  if [ -z "$TARGET" ]; then
    log "no tag matching $TAG_GLOB found, nothing to deploy"
    exit 0
  fi
else
  TARGET="$(git rev-parse --short "$REMOTE/$BRANCH")"
fi

if ! git rev-parse --verify --quiet "$TARGET^{commit}" >/dev/null; then
  log "ERROR: $TARGET does not resolve to a commit"
  exit 1
fi

TARGET_SHA="$(git rev-parse --short "$TARGET^{commit}")"
CURRENT="$(cat "$STATE_FILE" 2>/dev/null || echo unknown)"
log "remote tip=$TARGET_SHA (mode=$MODE) deployed=$CURRENT"

if [ "$TARGET_SHA" = "$CURRENT" ] && [ "$FORCE" -ne 1 ]; then
  log "already up to date, nothing to deploy"
  exit 0
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  log "--check given: $TARGET_SHA pending, stopping before deployment"
  exit 0
fi

PIPELINE="$SRC_DIR/deploy/ci/deploy-pipeline.sh"
if [ ! -f "$PIPELINE" ]; then
  PIPELINE="$REMOTE_DIR/deploy/ci/deploy-pipeline.sh"
fi

# Check out the pipeline before executing it. A running bash script keeps reading
# the file at byte offsets, so switching files underneath it mixes old and new
# code: without this step a pipeline that changes on this very commit still runs
# its own previous version (observed once as a stale quality-gate failure).
log "checking out $TARGET_SHA before handing over"
git -C "$SRC_DIR" checkout --detach "$TARGET_SHA" 2>&1 | sed 's/^/  git: /'

# ---------------------------------------------------------------- lightweight path
# A commit that only touches documentation or repository metadata cannot change
# runtime behaviour, but the full pipeline would still spend ~8 min on the gate
# and take the stack down for 1-2 min to rebuild identical images. Such changes
# are recorded and released without a rebuild. Set DEPLOY_ALWAYS_BUILD=1 (or pass
# --force) to override.
is_docs_only() {
  local files="$1" f
  [ -n "$files" ] || return 1
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    case "$f" in
      docs/* | *.md | README* | LICENSE | .gitignore | .gitattributes) ;;
      *) return 1 ;;
    esac
  done <<< "$files"
  return 0
}

CHANGED=""
if [ "$CURRENT" != "unknown" ] && git -C "$SRC_DIR" rev-parse --verify --quiet "$CURRENT^{commit}" >/dev/null; then
  CHANGED="$(git -C "$SRC_DIR" diff --name-only "$CURRENT" "$TARGET_SHA" 2>/dev/null || true)"
fi

if [ "${DEPLOY_ALWAYS_BUILD:-0}" != "1" ] && [ "$FORCE" -ne 1 ] && is_docs_only "$CHANGED"; then
  log "docs-only change ($(wc -l <<< "$CHANGED") files), recording without rebuild"
  # The scheduled timer executes the stable copy under $REMOTE_DIR, not $SRC_DIR,
  # so refresh it here; otherwise deploy/ci fixes would only land on the next
  # install-poller.sh run.
  mkdir -p "$REMOTE_DIR/deploy/ci"
  cp -a "$SRC_DIR"/deploy/ci/. "$REMOTE_DIR"/deploy/ci/
  echo "$TARGET_SHA" > "$STATE_FILE"
  echo "$(stamp) commit=$TARGET_SHA previous=$CURRENT elapsed=0s result=SUCCESS-LIGHTWEIGHT" \
    >> "$REMOTE_DIR/deploy/ci/releases.log"
  log "=== LIGHTWEIGHT DEPLOY FINISHED: $TARGET_SHA (no rebuild) ==="
  exit 0
fi

log "deploying $TARGET_SHA via $PIPELINE"

# shellcheck disable=SC2086 # PIPELINE_ARGS is intentionally word-split.
# Note: ${PIPELINE_ARGS:-} (not $PIPELINE_ARGS) -- under `set -u` an unset
# variable aborts the script before the pipeline ever starts, which is exactly
# what happened on the first scheduled run (timer has no env, manual runs did).
exec bash "$PIPELINE" "$TARGET_SHA" ${PIPELINE_ARGS:-}
