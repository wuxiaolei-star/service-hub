FROM ghcr.io/osgeo/gdal:ubuntu-small-3.10.3

ARG SOURCE_SHA256
LABEL org.opencontainers.image.title="nc_to_shp" \
      org.opencontainers.image.version="1.0.0" \
      io.python-service-hub.plugin-source-sha256="${SOURCE_SHA256}"

USER root
# The GDAL base image ships neither `pip` nor `ensurepip`, and Ubuntu 24.04 marks the
# system interpreter as externally managed (PEP 668). Bootstrap pip first, then install
# into the system environment on purpose: the Job container runs `/usr/bin/python3`
# directly, so the packages must be importable from the interpreter that the base image
# already provides (it is Python 3.12, matching plugin.yaml `runtime.python.version`).
RUN apt-get update \
    && apt-get install --no-install-recommends -y python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --no-cache-dir --break-system-packages --root-user-action=ignore \
      docker==7.1.0 \
      h5py==3.12.1 \
      numpy==2.1.3 \
      pydantic==2.10.4 \
      PyYAML==6.0.2 \
      scipy==1.14.1 \
      zstandard==0.23.0
COPY wheelhouse/ /tmp/wheelhouse/
RUN python3 -m pip install --no-cache-dir --break-system-packages --root-user-action=ignore \
      --no-deps /tmp/wheelhouse/*.whl \
    && python3 -c "import sys; assert sys.version_info[:2] == (3, 12)" \
    && python3 -c "import h5py, scipy, zstandard; from osgeo import ogr; import hub_runner" \
    && rm -rf /tmp/wheelhouse

COPY plugin.yaml /plugin/plugin.yaml
COPY plugin/ /plugin/
RUN chmod -R a=rX /plugin

USER 65532:65532
WORKDIR /
ENTRYPOINT ["python3", "-m", "hub_runner"]
