FROM python:3.12-slim

# Optional build-time index override. Empty means "use pip's default index", so
# the image builds identically on a machine with fast access to PyPI; a mirror
# only has to be passed where PyPI is slow (a cold build spent 26 minutes at
# 22 kB/s against pypi.org and 90 seconds against a regional mirror).
ARG PIP_INDEX_URL=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/packages/hub-server/src \
    HUB_CONFIG_PATH=/app/config/hub.yaml \
    HUB_DATA_ROOT=/data

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates gosu supervisor tini \
    && rm -rf /var/lib/apt/lists/*

COPY packages/hub-contracts packages/hub-contracts
COPY packages/hub-sdk packages/hub-sdk
COPY packages/hub-runner packages/hub-runner
COPY packages/hub-server packages/hub-server
RUN pip install --no-cache-dir ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} \
    ./packages/hub-contracts \
    ./packages/hub-sdk \
    ./packages/hub-runner \
    ./packages/hub-server

COPY alembic.ini ./
COPY alembic ./alembic
COPY config/hub.yaml ./config/hub.yaml
COPY deploy ./deploy
COPY docker-entrypoint.sh /usr/local/bin/service-hub-entrypoint
COPY deploy/service_hub/supervisord.conf /etc/service-hub/supervisord.conf

RUN groupadd --system --gid 65532 hub-data \
    && useradd --system --gid hub-data --home-dir /nonexistent --shell /usr/sbin/nologin hub-api \
    && useradd --system --gid hub-data --home-dir /nonexistent --shell /usr/sbin/nologin conda-runner \
    && useradd --system --gid hub-data --home-dir /nonexistent --shell /usr/sbin/nologin docker-runner \
    && useradd --system --gid hub-data --home-dir /nonexistent --shell /usr/sbin/nologin service-mgr \
    && mkdir -p /data /run/service-hub \
    && chmod 755 /usr/local/bin/service-hub-entrypoint

STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-m", "deploy.service_hub.healthcheck"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/service-hub-entrypoint"]
CMD ["supervisord", "-n", "-c", "/etc/service-hub/supervisord.conf"]
