# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /bin/uv

# Runtime deps are resolved and locked strictly from uv.lock frozen resolution
# so this image is deterministic and cannot drift across builds.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --no-cache --system --require-hashes -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

COPY . .

ENTRYPOINT ["python", "product_ops/deployment/cloud_run_job_entrypoint.py"]
CMD ["worker", "--max-jobs", "100"]
