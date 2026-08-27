# syntax=docker/dockerfile:1
# ODay Plus API (FastAPI) — served by uvicorn.
# The app (apps/api/oday_api/main.py:app) imports across the repo
# (modules/, shared/, solver/, models/, packages/), so the whole tree is copied
# and the repo root is placed on PYTHONPATH.
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

# App source (node_modules/.next/etc. excluded via .dockerignore).
COPY . .

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "apps.api.oday_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
