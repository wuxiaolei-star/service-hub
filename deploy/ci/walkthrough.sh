#!/usr/bin/env bash
# Business-level post-deploy acceptance for the deployment host.
#
# The health check only proves the processes are alive. This walkthrough proves the
# product still works end to end: log in, find the plugin registry entry, upload one
# real NetCDF sample, run one job against it and wait for SUCCESS.
#
# Credentials come from HUB_ADMIN_PASSWORD, or from $REMOTE_DIR/.deploy-credentials
# (single line "username:password", root-owned 0600, never in git). Without either the
# script says so and exits 0: a missing credential is not a deploy failure.
#
# Optional environment:
#   HUB_API_BASE               default http://127.0.0.1:18000/api/v1
#   HUB_WALKTHROUGH_PLUGIN     default nc_to_shp
#   HUB_WALKTHROUGH_VERSION    default: the plugin's latest_version from the registry
#   HUB_WALKTHROUGH_SAMPLE     default $REMOTE_DIR/samples/nc-sample.nc
#   HUB_WALKTHROUGH_TIMEOUT    job-wait budget in seconds, default 300
set -Eeuo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/service-hub}"
API_BASE="${HUB_API_BASE:-http://127.0.0.1:18000/api/v1}"
PLUGIN_ID="${HUB_WALKTHROUGH_PLUGIN:-nc_to_shp}"
SAMPLE="${HUB_WALKTHROUGH_SAMPLE:-$REMOTE_DIR/samples/nc-sample.nc}"
TIMEOUT="${HUB_WALKTHROUGH_TIMEOUT:-300}"
CREDENTIALS_FILE="$REMOTE_DIR/.deploy-credentials"

stamp() { date -Is; }
log() { echo "[$(stamp)] walkthrough: $*"; }

USERNAME="${HUB_ADMIN_USERNAME:-admin}"
PASSWORD="${HUB_ADMIN_PASSWORD:-}"
if [ -z "$PASSWORD" ] && [ -f "$CREDENTIALS_FILE" ]; then
  PASSWORD=$(tr -d '\r\n' < "$CREDENTIALS_FILE")
  USERNAME=${PASSWORD%%:*}
  PASSWORD=${PASSWORD#*:}
fi
if [ -z "$PASSWORD" ]; then
  log "no credentials (HUB_ADMIN_PASSWORD or $CREDENTIALS_FILE), skipping"
  exit 0
fi

TOKEN=$(curl -fsS -X POST "$API_BASE/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])') \
  || { log "login failed"; exit 1; }
AUTH="Authorization: Bearer $TOKEN"

PLUGINS=$(curl -fsS -H "$AUTH" "$API_BASE/plugins") \
  || { log "registry unavailable"; exit 1; }
echo "$PLUGINS" | python3 -c "
import json, sys
items = json.load(sys.stdin)['items']
ids = {item['id'] for item in items}
sys.exit(0 if '$PLUGIN_ID' in ids else 1)
" || { log "plugin $PLUGIN_ID missing from the registry"; exit 1; }

if [ -z "${HUB_WALKTHROUGH_VERSION:-}" ]; then
  PLUGIN_VERSION=$(echo "$PLUGINS" | python3 -c "
import json, sys
items = json.load(sys.stdin)['items']
print(next(item['latest_version'] for item in items if item['id'] == '$PLUGIN_ID'))
")
else
  PLUGIN_VERSION="$HUB_WALKTHROUGH_VERSION"
fi

if [ ! -f "$SAMPLE" ]; then
  # Fall back to the input of the most recent successful job so a missing sample
  # file does not permanently disable the walkthrough.
  SAMPLE=$(ls -1t "$REMOTE_DIR"/data/jobs/*/input/*.nc 2>/dev/null | head -n 1 || true)
  if [ -z "$SAMPLE" ] || [ ! -f "$SAMPLE" ]; then
    log "no sample NC file found, skipping"
    exit 0
  fi
fi

FILE_KEY=$(curl -fsS -X POST -H "$AUTH" -F "file=@$SAMPLE" "$API_BASE/files" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["file_key"])') \
  || { log "sample upload failed"; exit 1; }

JOB=$(curl -fsS -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"plugin_id\":\"$PLUGIN_ID\",\"version\":\"$PLUGIN_VERSION\",\"inputs\":{\"source_nc\":\"$FILE_KEY\"}}" \
  "$API_BASE/jobs") \
  || { log "job creation failed"; exit 1; }
JOB_KEY=$(echo "$JOB" | python3 -c 'import json, sys; print(json.load(sys.stdin)["job_id"])')
log "job $JOB_KEY created ($PLUGIN_ID@$PLUGIN_VERSION)"

STATUS="PENDING"
WAITED=0
while [ "$WAITED" -lt "$TIMEOUT" ]; do
  STATUS=$(curl -fsS -H "$AUTH" "$API_BASE/jobs/$JOB_KEY" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])')
  case "$STATUS" in
    SUCCESS | FAILED | CANCELLED) break ;;
  esac
  sleep 5
  WAITED=$((WAITED + 5))
done

# The uploaded sample has served its purpose either way; leaving it behind would
# grow storage quota on every deploy.
curl -fsS -X DELETE -H "$AUTH" "$API_BASE/files/$FILE_KEY" >/dev/null 2>&1 \
  || log "sample cleanup failed (non-fatal)"

if [ "$STATUS" != "SUCCESS" ]; then
  log "job $JOB_KEY ended as $STATUS after ${WAITED}s"
  exit 1
fi
log "job $JOB_KEY SUCCESS — business acceptance passed"
