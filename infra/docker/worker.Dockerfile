# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY pyproject.toml ./
RUN python -c "import tomllib, pathlib; deps = tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; pathlib.Path('/tmp/req.txt').write_text(chr(10).join(deps))" \
    && pip install --no-cache-dir -r /tmp/req.txt "alembic>=1.13" "psycopg[binary,pool]>=3.2" \
    && rm -f /tmp/req.txt

COPY . .

ENTRYPOINT ["python", "scripts/deployment/cloud_run_job_entrypoint.py"]
CMD ["worker", "--max-jobs", "100"]
