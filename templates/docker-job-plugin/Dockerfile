FROM python:3.12-slim

ARG SOURCE_SHA256
LABEL org.opencontainers.image.title="example_script" \
      org.opencontainers.image.version="1.0.0" \
      io.python-service-hub.plugin-source-sha256="${SOURCE_SHA256}"

COPY wheelhouse/ /tmp/wheelhouse/
RUN python3 -m pip install --no-cache-dir --no-deps /tmp/wheelhouse/*.whl \
    && python3 -c "import sys; assert sys.version_info[:2] == (3, 12)" \
    && python3 -c "import zstandard, pydantic, yaml, hub_runner" \
    && rm -rf /tmp/wheelhouse

COPY plugin.yaml /plugin/plugin.yaml
COPY plugin/ /plugin/
RUN chmod -R a=rX /plugin

USER 65532:65532
WORKDIR /
ENTRYPOINT ["python3", "-m", "hub_runner"]
