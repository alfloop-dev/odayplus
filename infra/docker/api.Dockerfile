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

# Runtime deps are installed straight from pyproject's [project] dependencies so
# this image can never drift out of sync with the code. A hand-maintained list
# here previously omitted deps that product code imported (e.g. httpx via the
# listing-feed adapter), crashing the API on boot and failing the E2E gate.
COPY pyproject.toml ./
RUN python -c "import tomllib, pathlib; deps = tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; pathlib.Path('/tmp/req.txt').write_text(chr(10).join(deps))" \
    && pip install --no-cache-dir -r /tmp/req.txt \
    && rm -f /tmp/req.txt

# App source (node_modules/.next/etc. excluded via .dockerignore).
COPY . .

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "apps.api.oday_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
