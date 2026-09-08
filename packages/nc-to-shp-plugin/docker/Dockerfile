FROM ghcr.io/osgeo/gdal:ubuntu-small-3.10.3

ARG SOURCE_SHA256
LABEL org.opencontainers.image.title="nc_to_shp" \
      org.opencontainers.image.version="1.0.0" \
      io.python-service-hub.plugin-source-sha256="${SOURCE_SHA256}"

USER root
RUN python3 -m pip install --no-cache-dir \
      docker==7.1.0 \
      h5py==3.12.1 \
      numpy==2.1.3 \
      pydantic==2.10.4 \
      PyYAML==6.0.2 \
      scipy==1.14.1 \
      zstandard==0.23.0
COPY wheelhouse/ /tmp/wheelhouse/
RUN python3 -m pip install --no-cache-dir --no-deps /tmp/wheelhouse/*.whl \
    && python3 -c "import sys; assert sys.version_info[:2] == (3, 12)" \
    && python3 -c "import h5py, scipy, zstandard; from osgeo import ogr; import hub_runner" \
    && rm -rf /tmp/wheelhouse

COPY plugin.yaml /plugin/plugin.yaml
COPY plugin/ /plugin/
RUN chmod -R a=rX /plugin

USER 65532:65532
WORKDIR /
ENTRYPOINT ["python3", "-m", "hub_runner"]
