#!/usr/bin/env bash
# Linux counterpart; creates only uniquely-named, labelled disposable test objects.
set -euo pipefail
deploy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
root_dir="$(dirname -- "$deploy_dir")"
model_dir="${1:?Pass an absolute model directory}"
model_manifest="${2:?Pass an absolute checksum manifest file}"
project="etl-portable-test-$(od -An -N5 -tx1 /dev/urandom | tr -d ' \n')"
scratch="$root_dir/.test-tmp/docker-portable/$project"
mkdir -p "$scratch/source"
pg_password="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
mongo_password="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
runtime_password="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
umask 077
cat > "$scratch/connections.json" <<EOF
{"DATABASE_URL":"postgresql+psycopg://etl_test_runtime:$runtime_password@master-test:5432/etl_test_portable_master","SOURCE_DATABASE_URL":"postgresql+psycopg://etl_test_reader:$runtime_password@source-test:5432/etl_test_portable_source","MONGO_URI":"mongodb://etl_test_runtime:$runtime_password@mongo-test:27017/etl_test_portable_content?authSource=etl_test_portable_content","MONGO_DATABASE":"etl_test_portable_content"}
EOF
# UID 10001 reads this new test-only file. Production secrets need a dedicated
# operator group/ACL, not world-readable files.
chgrp 10001 "$scratch/connections.json"
chmod 640 "$scratch/connections.json"
cat > "$scratch/test.env" <<EOF
TEST_PG_PASSWORD=$pg_password
TEST_MONGO_PASSWORD=$mongo_password
TEST_RUNTIME_PASSWORD=$runtime_password
MODEL_DIR=$model_dir
MODEL_MANIFEST_FILE=$model_manifest
QA_IMAGE=${ETL_QA_IMAGE:-etl-portable-qa:local}
BACKEND_IMAGE=${ETL_BACKEND_IMAGE:-etl-portable-backend:local}
FRONTEND_IMAGE=${ETL_FRONTEND_IMAGE:-etl-portable-frontend:local}
APP_PROJECT=$project-app
ETL_DATA_NETWORK=${project}_default
WEB_PORT=${ETL_TEST_WEB_PORT:-18080}
SOURCE_LOCAL_DIR=$scratch/source
CONNECTIONS_FILE=$scratch/connections.json
SOURCE_REFERENCE_ENABLED=true
SOURCE_TEMPORARY_FILES_ENABLED=true
INDEX_AUTO_ENABLED=false
EOF
test_compose=(docker compose --env-file "$scratch/test.env" -p "$project" -f "$deploy_dir/compose.test.yml")
app_compose=(docker compose --env-file "$scratch/test.env" -p "$project-app" -f "$deploy_dir/compose.app.yml" --profile workers)
record() {
    local name="$1"; shift
    local code=0
    "$@" > "$scratch/$name.raw" 2>&1 || code=$?
    sed -e "s/$pg_password/[TEST_SECRET]/g" -e "s/$mongo_password/[TEST_SECRET]/g" -e "s/$runtime_password/[TEST_SECRET]/g" "$scratch/$name.raw" > "$scratch/$name.log"
    printf '%s,%s\n' "$name" "$code" >> "$scratch/results.csv"
    echo "$name exit=$code"
    return "$code"
}
owned() {
    local expected="$1"; shift
    local id
    while IFS= read -r id; do
        [[ -z "$id" ]] && continue
        [[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id")" == "$expected" ]]
        [[ "$(docker inspect --format '{{index .Config.Labels "etl.portable.owner"}}' "$id")" == docker-portable-20261021 ]]
    done < <("$@" ps --all --quiet)
}
cleanup() {
    owned "$project-app" "${app_compose[@]}" && "${app_compose[@]}" down --timeout 420
    owned "$project" "${test_compose[@]}" && "${test_compose[@]}" down --timeout 30
    echo "Evidence: $scratch; named app test volumes retained."
}
trap cleanup EXIT
record compose-config "${test_compose[@]}" config --quiet
record isolated-db-start "${test_compose[@]}" up -d --wait --wait-timeout 120 master-test source-test mongo-test
owned "$project" "${test_compose[@]}"
record bootstrap-owned-empty-test "${test_compose[@]}" run --rm --no-deps -e ETL_APPROVE_EMPTY_TEST_BOOTSTRAP=docker-portable-20261021 qa python /app/deploy/tests/bootstrap_isolated.py
record native-unit-acceptance "${test_compose[@]}" run --rm --no-deps -e ETL_DOCKER_NATIVE_TEST=1 -e ETL_REQUIRE_POSTGRES=0 -e INDEX_EMBEDDING_MODEL_PATH= -e INDEX_EMBEDDING_MODEL_ID= -e INDEX_EMBEDDING_MODEL_VERSION= qa python -m pytest /app/deploy/tests /app/tests/unit/test_admin_boundary.py /app/tests/unit/test_search_access.py /app/tests/unit/test_single_admin_auth.py /app/tests/unit/test_source_reference.py /app/tests/unit/test_ocr_readiness.py /app/tests/unit/test_multiformat.py /app/tests/unit/test_file_preview.py -q --tb=short --basetemp=/tmp/etl-qa/native
record source-ocr-index-search-real "${test_compose[@]}" run --rm --no-deps -e ETL_DOCKER_NATIVE_TEST=1 qa python -m pytest /app/tests/integration/source_postgres/test_docker_portable.py -q --tb=short --basetemp=/tmp/etl-qa/integration
record db-readonly-schema-check "${app_compose[@]}" --profile ops run --rm --no-deps preflight
record app-five-services "${app_compose[@]}" up -d --no-build --wait --wait-timeout 240 api frontend master-sync-worker source-file-worker ocr-worker
owned "$project-app" "${app_compose[@]}"
for worker in master-sync-worker source-file-worker ocr-worker; do
    record "$worker-heartbeat" "${app_compose[@]}" exec -T "$worker" python /opt/etl/runtime.py health-worker
done
# HTTP is tested from the API container against nginx over the private network;
# use Host localhost so the caller does not mint a localhost peer/grant.
record proxy-boundary "${app_compose[@]}" exec -T api python -c 'import urllib.request,urllib.error; base="http://frontend:8080"; r=urllib.request.Request(base+"/health",headers={"Host":"localhost"}); assert urllib.request.urlopen(r,timeout=10).status==200; r=urllib.request.Request(base+"/api/v1/resources",headers={"Host":"localhost"});
try: urllib.request.urlopen(r,timeout=10); raise AssertionError("guard unexpectedly allowed")
except urllib.error.HTTPError as e: assert e.code==403'
record sigterm-restart "${app_compose[@]}" restart master-sync-worker source-file-worker ocr-worker
echo "PASS: $project on this Linux Docker engine; this is not a second-host acceptance."
