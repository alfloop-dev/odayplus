FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5000

WORKDIR /srv/oday-plus

COPY infra/mlflow/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --requirement /tmp/requirements.txt \
    && python -c "import mlflow, psycopg2" \
    && groupadd --system --gid 10001 mlflow \
    && useradd --system --uid 10001 --gid mlflow --home-dir /nonexistent mlflow

COPY infra/mlflow /srv/oday-plus/infra/mlflow

USER 10001:10001
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-m", "infra.mlflow.healthcheck", "--mode", "health"]

ENTRYPOINT ["python", "-m", "infra.mlflow.entrypoint"]
