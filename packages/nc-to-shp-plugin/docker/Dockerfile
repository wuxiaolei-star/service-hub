FROM ghcr.io/osgeo/gdal:ubuntu-small-3.10.3

ARG SOURCE_SHA256
LABEL org.opencontainers.image.title="nc_to_shp" \
      org.opencontainers.image.version="1.0.0" \
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
RUN apt-get update \
    && apt-get install --no-install-recommends -y python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir \
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
