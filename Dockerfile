FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY alembic ./alembic
COPY fixtures ./fixtures

FROM base AS qa
RUN pip install --no-cache-dir '.[dev]'
COPY tests ./tests
COPY scripts ./scripts
COPY docs ./docs
COPY .env.example .gitignore .dockerignore docker-compose.yml Dockerfile ./
CMD ["python", "scripts/verify.py"]

FROM base AS runtime
ENV ADMIN_ENABLED=false STORAGE_ROOT=/app/storage
RUN groupadd --gid 10001 etl && useradd --uid 10001 --gid etl --no-create-home etl \
    && mkdir /app/storage && chown etl:etl /app/storage
USER etl
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["python", "-m", "uvicorn", "data_sync_etl.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
