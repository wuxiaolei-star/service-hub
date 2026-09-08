FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/packages/hub-server/src \
    HUB_CONFIG_PATH=/app/config/hub.yaml

WORKDIR /app

COPY packages/hub-server/pyproject.toml packages/hub-server/pyproject.toml
COPY packages/hub-contracts packages/hub-contracts
COPY packages/hub-server/src packages/hub-server/src
RUN pip install --no-cache-dir ./packages/hub-contracts ./packages/hub-server

COPY alembic.ini ./
COPY alembic ./alembic
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint

RUN groupadd --system --gid 65532 hub \
    && useradd --system --uid 65532 --gid 65532 --home-dir /app \
        --shell /usr/sbin/nologin hub \
    && mkdir -p /app/config /data \
    && chown -R hub:hub /app /data \
    && chmod 755 /usr/local/bin/docker-entrypoint

ENTRYPOINT ["docker-entrypoint"]
CMD ["uvicorn", "hub_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
