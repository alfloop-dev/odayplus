# syntax=docker/dockerfile:1.7

ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}

ARG PYTHON_BASE_IMAGE
ARG ODP_RELEASE_SHA

LABEL org.opencontainers.image.title="ODay Plus production data platform" \
      org.opencontainers.image.revision="${ODP_RELEASE_SHA}" \
      org.opencontainers.image.source="https://github.com/alfloop-dev/odayplus"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HOME=/var/lib/oday \
    XDG_CACHE_HOME=/var/lib/oday/cache \
    DLT_PIPELINES_DIR=/var/lib/oday/dlt

WORKDIR /app

RUN base_digest="${PYTHON_BASE_IMAGE##*@sha256:}" \
    && test "${PYTHON_BASE_IMAGE}" != "${base_digest}" \
    && case "${base_digest}" in \
      *[!0-9a-f]*|"") exit 1 ;; \
      *) test "${#base_digest}" -eq 64 ;; \
    esac \
    && case "${ODP_RELEASE_SHA}" in \
      ????????-????-????-????-????????????) exit 1 ;; \
      *[!0-9a-f]*|"") exit 1 ;; \
      *) test "${#ODP_RELEASE_SHA}" -eq 40 ;; \
    esac \
    && python -m pip install --no-cache-dir \
      "alembic==1.18.5" \
      "dagster==1.13.15" \
      "dlt[postgres]==1.29.1" \
      "psycopg[binary,pool]==3.3.4" \
      "pymongo==4.17.0"

COPY apps/data_platform /app/apps/data_platform
COPY scripts/data_platform /app/scripts/data_platform
COPY shared /app/shared
COPY infra/db/migrations /app/infra/db/migrations
COPY docs/data /app/docs/data
COPY scripts/validate_assisted_listing_intake_schema.sql \
  /app/scripts/validate_assisted_listing_intake_schema.sql
COPY infra/k8s/data-platform/runtime /opt/oday/deployment

RUN mkdir -p /var/lib/oday/cache /var/lib/oday/dlt /var/run/oday \
    && chown -R 65532:65532 /var/lib/oday /var/run/oday \
    && python -m compileall -q \
      /app/apps/data_platform \
      /app/scripts/data_platform \
      /opt/oday/deployment

USER 65532:65532

ENTRYPOINT ["python", "/opt/oday/deployment/deployment_runtime.py"]
CMD ["--help"]
