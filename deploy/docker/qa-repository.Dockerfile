# syntax=docker/dockerfile:1
# Optional repository audit image. Never used by compose.app.yml runtime services.
ARG QA_IMAGE=etl-portable-backend:qa
FROM ${QA_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /app
WORKDIR /app
# Keep Git metadata and all local secrets outside the image.
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
COPY deploy ./deploy
COPY deploy/runtime /opt/etl
COPY Dockerfile ./Dockerfile
COPY migrations_mongo ./migrations_mongo
COPY frontend/src ./frontend/src
COPY frontend/public ./frontend/public
COPY docs/audits/erd-contract/contract_snapshot.json ./docs/audits/erd-contract/contract_snapshot.json
COPY docs/audits/master-migration/decisions.json ./docs/audits/master-migration/decisions.json
COPY docs/audits/api-hardening/openapi_before.json ./docs/audits/api-hardening/openapi_before.json
# These eight reference-only artifacts must be provided for ERD governance QA.
COPY TAI_LIEU_DOI_CHIEU/ ./TAI_LIEU_DOI_CHIEU/
USER 10001:10001
WORKDIR /tmp/etl-qa
