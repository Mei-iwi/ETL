# Isolated analyzer compatibility check; no services, DB or model mounts.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
USER root
WORKDIR /app
RUN python -m pip install --no-cache-dir underthesea==8.3.0 'pydantic>=2.9,<3' 'python-ulid>=3,<4'
COPY src/data_sync_etl/domain ./src/data_sync_etl/domain
COPY scripts/check_lexical_analyzer.py ./scripts/check_lexical_analyzer.py
ENV PYTHONPATH=/app/src
ENTRYPOINT ["python", "scripts/check_lexical_analyzer.py"]
