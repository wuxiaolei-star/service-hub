FROM ghcr.io/osgeo/gdal:ubuntu-small-3.10.3

ARG SOURCE_SHA256
# Optional PyPI index override. Left empty, pip uses its own default (pypi.org), so an
# offline or air-gapped build behaves exactly as before. A build can point this at a
# closer mirror instead, which on the reference host is worth two orders of magnitude:
# pypi.org served these wheels at ~34 kB/s while mirrors.aliyun.com managed ~13 MB/s,
# turning a ~40 minute dependency install into roughly one minute.
ARG PIP_INDEX_URL=""
# Same idea for the distro archive. The base image is Ubuntu, whose sources point at
# archive.ubuntu.com; on the reference host that mirror answers at a few tens of kB/s
# (~350 s per package), while a regional mirror finishes the apt layer in seconds.
# Only the host part is rewritten, so APT_MIRROR stays a mirror root exactly like in
# the main Dockerfile (https://mirrors.aliyun.com, not .../ubuntu).
ARG APT_MIRROR=""
LABEL org.opencontainers.image.title="nc_to_shp" \
      org.opencontainers.image.version="1.0.1" \
      io.python-service-hub.plugin-source-sha256="${SOURCE_SHA256}"

# The base image provides the GDAL Python bindings (osgeo) through the distro, but it
# ships no pip and marks its interpreter as externally managed (Ubuntu 24.04, PEP 668).
# It also carries an apt-owned numpy 1.26.4 in /usr/lib/python3/dist-packages: pip cannot
# uninstall that (no RECORD file), so installing numpy 2.1.3 straight into the system
# interpreter fails with "Cannot uninstall numpy 1.26.4".
#
# A venv with --system-site-packages solves all three at once: pip owns everything it
# installs, the distro's numpy is shadowed instead of uninstalled, and osgeo stays
# importable from the parent interpreter.
USER root
# Ubuntu 24.04 keeps its sources in the deb822 ubuntu.sources file; older or slim
# variants use /etc/apt/sources.list instead. Both are rewritten when a mirror is set,
# and an unset mirror leaves the layer byte-identical to an offline build. The apt
# caches live in BuildKit cache mounts (docker 23+ enables BuildKit by default), so
# repeated builds skip the archive download and the lists never enter the layer.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list; do \
        if [ -f "$f" ]; then \
          sed -i -E "s#https?://(archive|ports|security)\.ubuntu\.com#${APT_MIRROR}#g" "$f"; \
        fi; \
      done; \
    fi; \
    apt-get update \
    && apt-get install --no-install-recommends -y python3-venv \
    && python3 -m venv --system-site-packages /opt/venv
# Kept as its own layer so swapping the mirror does not redo the apt work above.
# The pip download cache persists across builds in a cache mount.
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -n "$PIP_INDEX_URL" ]; then \
      /opt/venv/bin/python -m pip config set global.index-url "$PIP_INDEX_URL"; \
    fi \
    && /opt/venv/bin/python -m pip install \
      docker==7.1.0 \
      h5py==3.12.1 \
      numpy==2.1.3 \
      pydantic==2.10.4 \
      PyYAML==6.0.2 \
      scipy==1.14.1 \
      zstandard==0.23.0
COPY wheelhouse/ /tmp/wheelhouse/
RUN /opt/venv/bin/python -m pip install --no-cache-dir --no-deps /tmp/wheelhouse/*.whl \
    && /opt/venv/bin/python -c "import sys; assert sys.version_info[:2] == (3, 12)" \
    && /opt/venv/bin/python -c "import h5py, scipy, zstandard; from osgeo import ogr; import hub_runner" \
    && rm -rf /tmp/wheelhouse

COPY plugin.yaml /plugin/plugin.yaml
COPY plugin/ /plugin/
# The Job container runs as 65532:65532 with a read-only rootfs, so every interpreter
# path it has to touch must be world-readable and traversable.
RUN chmod -R a=rX /plugin /opt/venv

USER 65532:65532
WORKDIR /
ENTRYPOINT ["/opt/venv/bin/python", "-m", "hub_runner"]
