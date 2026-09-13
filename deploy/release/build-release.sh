#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="python-service-hub:1.0.0-linux-amd64"
WEB_IMAGE_NAME="python-service-hub-web:1.0.0-linux-amd64"
RELEASE_NAME="service-hub-linux-amd64"
ARCHIVE_PATH="dist/service-hub-1.0.0-linux-amd64.tar.gz"

machine="$(uname -m)"
case "${machine}" in
    x86_64|amd64) ;;
    *)
        echo "This release must be built on Linux AMD64; got ${machine}" >&2
        exit 1
        ;;
esac

command -v docker >/dev/null 2>&1 || {
    echo "docker is required" >&2
    exit 1
}
command -v sha256sum >/dev/null 2>&1 || {
    echo "sha256sum is required" >&2
    exit 1
}
command -v tar >/dev/null 2>&1 || {
    echo "tar is required" >&2
    exit 1
}

release_workdir="$(mktemp -d)"
cleanup() {
    if [ -n "${release_workdir:-}" ] && [ -d "${release_workdir}" ]; then
        case "${release_workdir}" in
            /tmp/*|/var/tmp/*)
                chmod -R u+w "${release_workdir}" 2>/dev/null || true
                rm -r "${release_workdir}"
                ;;
        esac
    fi
}
trap cleanup EXIT INT TERM

bundle_dir="${release_workdir}/${RELEASE_NAME}"
mkdir -p "${bundle_dir}" "dist"

echo "Building ${IMAGE_NAME}"
docker build --platform linux/amd64 -t "${IMAGE_NAME}" .

echo "Building ${WEB_IMAGE_NAME}"
docker build --platform linux/amd64 -t "${WEB_IMAGE_NAME}" web

echo "Saving images"
docker save -o "${bundle_dir}/service-hub-image.tar" "${IMAGE_NAME}"
docker save -o "${bundle_dir}/service-hub-web-image.tar" "${WEB_IMAGE_NAME}"

cp compose.yaml "${bundle_dir}/compose.yaml"
cp deploy/release/install.sh "${bundle_dir}/install.sh"
cp deploy/release/start.sh "${bundle_dir}/start.sh"
cp deploy/release/stop.sh "${bundle_dir}/stop.sh"
cp deploy/release/status.sh "${bundle_dir}/status.sh"
cp tools/hubctl "${bundle_dir}/hubctl"
chmod 0755 \
    "${bundle_dir}/install.sh" \
    "${bundle_dir}/start.sh" \
    "${bundle_dir}/stop.sh" \
    "${bundle_dir}/status.sh" \
    "${bundle_dir}/hubctl"

cat >"${bundle_dir}/README.md" <<'README'
# Service Hub Linux AMD64 Offline Bundle

Run these commands from this extracted directory:

```bash
sudo ./install.sh
sudo ./start.sh
sudo ./status.sh
```

Set `HUB_HOST_DATA_DIR` before `install.sh` to use a data directory other than
`/srv/service-hub-data`. Re-running the scripts preserves the existing `.env`
and Service Hub data.
README

(
    cd "${bundle_dir}"
    sha256sum \
        service-hub-image.tar \
        service-hub-web-image.tar \
        compose.yaml \
        install.sh \
        start.sh \
        stop.sh \
        status.sh \
        hubctl \
        README.md > SHA256SUMS
)

tar -czf "${ARCHIVE_PATH}" -C "${release_workdir}" "${RELEASE_NAME}"

echo "Wrote ${ARCHIVE_PATH}"
sha256sum "${ARCHIVE_PATH}"
