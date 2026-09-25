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

log "deploying $TARGET_SHA via $PIPELINE"

# shellcheck disable=SC2086 # PIPELINE_ARGS is intentionally word-split.
exec bash "$PIPELINE" "$TARGET_SHA" $PIPELINE_ARGS
