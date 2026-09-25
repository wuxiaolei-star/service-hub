#!/usr/bin/env bash
# Install the pull-based deployment poller on the host.
#
# The runner is a systemd timer, not cron: it survives reboots, keeps logs in
# the journal and will not start a second deployment while one is running
# (pull-deploy.sh itself takes a flock, so overlapping runs are a no-op).
#
# Usage:
#   install-poller.sh [--interval=3min] [--branch=main] [--mode=branch]
set -Eeuo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/service-hub}"
SRC_DIR="$REMOTE_DIR/src"
INTERVAL="3min"
BRANCH="main"
MODE="branch"

for arg in "$@"; do
  case "$arg" in
    --interval=*) INTERVAL="${arg#--interval=}" ;;
    --branch=*) BRANCH="${arg#--branch=}" ;;
    --mode=*) MODE="${arg#--mode=}" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root (systemd units live in /etc)" >&2
  exit 1
fi

UNIT_DIR="/etc/systemd/system"
mkdir -p "$REMOTE_DIR/deploy/ci"

# Stable copy of the entry point: the timer always calls this path, while the
# pipeline itself is executed from the freshly checked-out source tree so that
# improvements to the pipeline ship with the code that uses them.
install -m 0755 "$SRC_DIR/deploy/ci/pull-deploy.sh" "$REMOTE_DIR/deploy/ci/pull-deploy.sh"
if [ -f "$SRC_DIR/deploy/ci/deploy-pipeline.sh" ]; then
  install -m 0755 "$SRC_DIR/deploy/ci/deploy-pipeline.sh" "$REMOTE_DIR/deploy/ci/deploy-pipeline.sh"
fi

sed -e "s#^OnUnitActiveSec=.*#OnUnitActiveSec=$INTERVAL#" \
    "$SRC_DIR/deploy/ci/systemd/service-hub-deploy.timer" > "$UNIT_DIR/service-hub-deploy.timer"
sed -e "s#^Environment=DEPLOY_BRANCH=.*#Environment=DEPLOY_BRANCH=$BRANCH#" \
    -e "s#^Environment=DEPLOY_MODE=.*#Environment=DEPLOY_MODE=$MODE#" \
    "$SRC_DIR/deploy/ci/systemd/service-hub-deploy.service" > "$UNIT_DIR/service-hub-deploy.service"

systemctl daemon-reload
systemctl enable --now service-hub-deploy.timer

cat <<EOF

installed:
  $(systemctl show -p FragmentPath --value service-hub-deploy.timer)
  interval=$INTERVAL branch=$BRANCH mode=$MODE

watch it:
  systemctl status service-hub-deploy.timer
  journalctl -u service-hub-deploy.service -f
  bash $REMOTE_DIR/deploy/ci/pull-deploy.sh --check    # dry run, no deploy

stop it:
  systemctl disable --now service-hub-deploy.timer
EOF
