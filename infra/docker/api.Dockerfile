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

# Runtime deps: fastapi is declared in pyproject; uvicorn is the ASGI server.
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" "pyyaml>=6.0.3" "pydantic>=2.8" "numpy>=2.0" "scikit-learn>=1.5" "statsmodels>=0.14" "h3>=4.5.0" "ortools>=9.15.6755" "duckdb>=1.0" "sqlalchemy>=2.0"

# App source (node_modules/.next/etc. excluded via .dockerignore).
COPY . .

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "apps.api.oday_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
