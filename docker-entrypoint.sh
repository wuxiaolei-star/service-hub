#!/bin/sh
set -eu

mkdir -p /data
chown -R hub:hub /data
exec runuser -u hub -- "$@"
