#!/bin/sh
set -eu

data_root=/data
socket_path=/var/run/docker.sock
permissions_marker="$data_root/.permissions-single-container-v1"

python -m deploy.service_hub.bootstrap
HUB_RUNNER_TOKEN=$(python -m deploy.service_hub.entrypoint read-runtime-token /run/service-hub/runtime.env)
export HUB_RUNNER_TOKEN
export HUB_INTERNAL_BASE_URL=http://127.0.0.1:8000/internal/v1
export HUB_DATA_ROOT="$data_root"
export HUB_DOCKER_HOST_DATA_ROOT="$HUB_HOST_DATA_DIR"

if [ ! -S "$socket_path" ]; then
    printf '%s\n' 'service-hub-entrypoint: Docker socket is required' >&2
    exit 1
fi

socket_gid=$(stat -c '%g' "$socket_path")
hub_data_gid=$(getent group hub-data | cut -d: -f3 || true)
if [ -z "$hub_data_gid" ]; then
    printf '%s\n' 'service-hub-entrypoint: hub-data group is required' >&2
    exit 1
fi
python -m deploy.service_hub.entrypoint check-socket-gid "$socket_gid" "$hub_data_gid"
socket_group=$(getent group "$socket_gid" | cut -d: -f1 || true)
if [ -z "$socket_group" ]; then
    socket_group=docker-socket
    groupadd --gid "$socket_gid" "$socket_group"
fi
# Only the two socket consumers join the Docker socket group: docker-runner for
# one-shot plugin Jobs and service-manager (V3.0) for long-running services.
# hub-api and conda-runner are deliberately left out.
usermod -aG "$socket_group" docker-runner
usermod -aG "$socket_group" service-mgr

if [ ! -f "$permissions_marker" ]; then
    mkdir -p \
        "$data_root/db" \
        "$data_root/files" \
        "$data_root/plugins" \
        "$data_root/jobs" \
        "$data_root/logs" \
        "$data_root/environments"

    # hub-api writes the one-time admin credential to the data root, which Docker
    # creates as root:root. Without group write access there the credential cannot
    # be written at all, and a fresh deployment ends up with no usable admin
    # account. The setgid bit keeps the group on files the runtime creates here.
    chgrp hub-data "$data_root"
    chmod 2775 "$data_root"

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
