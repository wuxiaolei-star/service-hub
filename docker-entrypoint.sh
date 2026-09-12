#!/bin/sh
set -eu

data_root=/data
socket_path=/var/run/docker.sock
permissions_marker="$data_root/.permissions-single-container-v1"

python -m deploy.service_hub.bootstrap
. /run/service-hub/runtime.env
export HUB_RUNNER_TOKEN
export HUB_INTERNAL_BASE_URL=http://127.0.0.1:8000/internal/v1
export HUB_DATA_ROOT="$data_root"
export HUB_DOCKER_HOST_DATA_ROOT="$HUB_HOST_DATA_DIR"

if [ ! -S "$socket_path" ]; then
    printf '%s\n' 'service-hub-entrypoint: Docker socket is required' >&2
    exit 1
fi

socket_gid=$(stat -c '%g' "$socket_path")
socket_group=$(getent group "$socket_gid" | cut -d: -f1 || true)
if [ -z "$socket_group" ]; then
    socket_group=docker-socket
    groupadd --gid "$socket_gid" "$socket_group"
fi
usermod -aG "$socket_group" docker-runner

if [ ! -f "$permissions_marker" ]; then
    mkdir -p \
        "$data_root/db" \
        "$data_root/files" \
        "$data_root/plugins" \
        "$data_root/jobs" \
        "$data_root/logs" \
        "$data_root/environments"

    chown -R hub-api:hub-data \
        "$data_root/db" \
        "$data_root/files" \
        "$data_root/plugins" \
        "$data_root/jobs" \
        "$data_root/logs"
    chown -R conda-runner:hub-data "$data_root/environments"
    chmod -R g+rwX \
        "$data_root/db" \
        "$data_root/files" \
        "$data_root/plugins" \
        "$data_root/jobs" \
        "$data_root/logs" \
        "$data_root/environments"
    find \
        "$data_root/db" \
        "$data_root/files" \
        "$data_root/plugins" \
        "$data_root/jobs" \
        "$data_root/logs" \
        "$data_root/environments" \
        -type d -exec chmod g+s {} +
    touch "$permissions_marker"
    chown hub-api:hub-data "$permissions_marker"
    chmod 664 "$permissions_marker"
fi

exec supervisord -n -c /etc/service-hub/supervisord.conf
