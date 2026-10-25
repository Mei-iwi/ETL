# Disposable QA only: no published ports, host source read-only, private network.
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskResults = Join-Path $taskRoot 'research\metadata-retrieval'
New-Item -ItemType Directory -Force -Path $taskResults | Out-Null
$taskSuffix = [Guid]::NewGuid().ToString('N').Substring(0, 10)
$taskNetwork = "etl-metadata-test-$taskSuffix"
$taskPg = "etl-metadata-pg-$taskSuffix"
$taskMongo = "etl-metadata-mongo-$taskSuffix"
try {
    docker network create --internal $taskNetwork | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated test network' }
    docker run --rm -d --name $taskPg --network $taskNetwork --network-alias postgres -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=etl_metadata_test postgres:16 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot start disposable PostgreSQL' }
    docker run --rm -d --name $taskMongo --network $taskNetwork --network-alias metadata-mongo mongo:8.0 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot start disposable MongoDB' }
    $taskReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        docker exec $taskPg pg_isready -U postgres 2>$null | Out-Null
        $taskPgReady = $LASTEXITCODE -eq 0
        docker exec $taskMongo mongosh --quiet --eval 'db.adminCommand({ping:1}).ok' 2>$null | Out-Null
        if ($taskPgReady -and $LASTEXITCODE -eq 0) { $taskReady = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $taskReady) { throw 'Disposable databases did not become ready' }
    docker run --rm --network $taskNetwork --workdir /work `
        --mount "type=bind,source=$taskRoot,target=/work,readonly" `
        --mount "type=bind,source=$taskResults,target=/results" `
        -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/work/src `
        -e TEST_DATABASE_URL=postgresql+psycopg://postgres@postgres:5432/etl_metadata_test `
        -e TEST_MONGO_URI=mongodb://metadata-mongo:27017 `
        etl-metadata-qa:local python -m pytest -q -p no:cacheprovider --tb=short `
        tests/unit/test_metadata_search.py tests/unit/test_metadata_media_registration.py `
        tests/unit/test_metadata_content_integration.py tests/integration/test_metadata_search_storage.py `
        tests/unit/test_metadata_http.py `
        tests/integration/test_metadata_search_postgres.py --junitxml=/results/docker-current.xml
    $taskExit = $LASTEXITCODE
} finally {
    # Names are literal handles created above, never production containers.
    docker rm -f $taskPg $taskMongo 2>$null | Out-Null
    docker network rm $taskNetwork 2>$null | Out-Null
}
if ($taskExit -ne 0) { throw "Disposable metadata QA failed: $taskExit" }
