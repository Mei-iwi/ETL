# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE} AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY deploy/docker/constraints.txt /build/constraints.txt
COPY deploy/docker/constraints.semantic.txt /build/constraints.semantic.txt
RUN pip install --upgrade pip && pip install 'torch==2.14.1+cpu' --index-url https://download.pytorch.org/whl/cpu
COPY pyproject.toml README.md ./
COPY src ./src
ARG INSTALL_SEMANTIC=false
ARG INSTALL_MEDIA=false
RUN if [ "$INSTALL_SEMANTIC" = "true" ]; then \
      pip install -c constraints.semantic.txt '.[embeddings,semantic,google-drive]'; \
    else pip install -c constraints.txt '.[embeddings,google-drive]'; fi && \
    if [ "$INSTALL_MEDIA" = "true" ]; then pip install '.[media,translation]'; fi && \
    pip check && pip freeze > /opt/venv/dependencies.txt

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
    HF_HOME=/run/etl/hf XDG_CACHE_HOME=/run/etl/cache HOME=/run/etl/home TMPDIR=/run/etl \
    AUTH_MODE=single_admin ADMIN_ENABLED=false SEARCH_INTERNAL_DEV_ENABLED=false APP_ENVIRONMENT=production \
    STORAGE_ROOT=/var/lib/etl/local SOURCE_TEMP_ROOT=/run/etl/source \
    TESSERACT_PATH=/usr/bin/tesseract TESSERACT_LANGUAGES=vie+eng OCR_ENGINE=tesseract \
    OFFICE_PDF_EXECUTABLE=/usr/bin/libreoffice OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates tini ffmpeg tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie \
      libreoffice-writer libreoffice-impress fonts-dejavu-core fonts-liberation2 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 etl && useradd --uid 10001 --gid 10001 --no-create-home etl \
    && mkdir -p /app /var/lib/etl/local/evaluations /var/lib/etl/auth /run/etl /opt/etl \
    && chown -R 10001:10001 /var/lib/etl /run/etl
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY src ./src
COPY alembic.ini alembic_source.ini ./
COPY alembic ./alembic
COPY alembic_source ./alembic_source
COPY deploy/runtime /opt/etl
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=6s --start-period=120s --retries=3 CMD ["python", "/opt/etl/runtime.py", "health-api"]
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "python", "/opt/etl/runtime.py"]
CMD ["api"]

FROM runtime AS qa
USER root
RUN pip install --no-cache-dir 'pytest>=8,<10' 'pytest-asyncio>=0.24,<2' 'httpx>=0.27,<1' 'ruff>=0.9,<1' 'PyYAML==6.0.3'
COPY pyproject.toml ./
COPY tests ./tests
COPY fixtures ./fixtures
COPY scripts ./scripts
COPY deploy ./deploy
RUN mkdir -p /tmp/etl-qa && chown 10001:10001 /tmp/etl-qa
USER 10001:10001
ENV PYTHONPATH=/app:/app/src PYTEST_ADDOPTS="-o cache_dir=/tmp/etl-qa/cache" AUTH_MODE=legacy_local
WORKDIR /tmp/etl-qa
HEALTHCHECK NONE
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["python", "-m", "pytest", "/app/deploy/tests", "-q", "--tb=short", "--basetemp=/tmp/etl-qa/pytest"]
