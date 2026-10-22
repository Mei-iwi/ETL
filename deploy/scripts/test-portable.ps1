param(
    [Parameter(Mandatory=$true)][string]$ModelDirectory,
    [Parameter(Mandatory=$true)][string]$ModelManifest,
    [string]$BackendImage='etl-portable-backend:local',
    [string]$FrontendImage='etl-portable-frontend:local',
    [string]$QaImage='etl-portable-qa:local',
    [switch]$KeepRunning
)
$ErrorActionPreference='Stop'
$deployDir=Split-Path -Parent $PSScriptRoot
$repoDir=Split-Path -Parent $deployDir
$project='etl-portable-test-' + [guid]::NewGuid().ToString('N').Substring(0,10)
$scratch=Join-Path $repoDir ('.test-tmp/docker-portable/' + $project)
New-Item -ItemType Directory -Path $scratch -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $scratch 'source') -Force | Out-Null
$password=[guid]::NewGuid().ToString('N')
$mongoPassword=[guid]::NewGuid().ToString('N')
$runtimePassword=[guid]::NewGuid().ToString('N')
$listener=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
$listener.Start(); $webPort=$listener.LocalEndpoint.Port; $listener.Stop()
$environment=Join-Path $scratch 'test.env'
$connections=Join-Path $scratch 'connections.json'
@{
    DATABASE_URL="postgresql+psycopg://etl_test_runtime:$runtimePassword@master-test:5432/etl_test_portable_master"
    SOURCE_DATABASE_URL="postgresql+psycopg://etl_test_reader:$runtimePassword@source-test:5432/etl_test_portable_source"
    MONGO_URI="mongodb://etl_test_runtime:$runtimePassword@mongo-test:27017/etl_test_portable_content?authSource=etl_test_portable_content"
    MONGO_DATABASE='etl_test_portable_content'
} | ConvertTo-Json | Set-Content -LiteralPath $connections -Encoding ascii
$model=(Resolve-Path -LiteralPath $ModelDirectory).Path.Replace('\','/')
$manifest=(Resolve-Path -LiteralPath $ModelManifest).Path.Replace('\','/')
$settings=@(
    "TEST_PG_PASSWORD=$password", "TEST_MONGO_PASSWORD=$mongoPassword", "TEST_RUNTIME_PASSWORD=$runtimePassword",
    "MODEL_DIR=$model", "MODEL_MANIFEST_FILE=$manifest", "QA_IMAGE=$QaImage", "BACKEND_IMAGE=$BackendImage", "FRONTEND_IMAGE=$FrontendImage",
    "APP_PROJECT=$project-app", "ETL_DATA_NETWORK=$($project)_default", "WEB_PORT=$webPort",
    "SOURCE_LOCAL_DIR=$($scratch.Replace('\','/'))/source", "CONNECTIONS_FILE=$($connections.Replace('\','/'))",
    'SOURCE_REFERENCE_ENABLED=true','SOURCE_TEMPORARY_FILES_ENABLED=true','INDEX_AUTO_ENABLED=false'
)
$settings | Set-Content -LiteralPath $environment -Encoding ascii
$testArgs=@('compose','--env-file',$environment,'-p',$project,'-f',(Join-Path $deployDir 'compose.test.yml'))
$appArgs=@('compose','--env-file',$environment,'-p',($project+'-app'),'-f',(Join-Path $deployDir 'compose.app.yml'),'--profile','workers')
$evidence=[System.Collections.Generic.List[object]]::new()
function Invoke-Recorded([string]$Name,[string[]]$Arguments) {
    $previousPreference=$ErrorActionPreference
    $ErrorActionPreference='Continue'
    $output=@(& docker @Arguments 2>&1 | ForEach-Object { $_.ToString() })
    $code=$LASTEXITCODE
    $ErrorActionPreference=$previousPreference
    $safe=($output -join "`n").Replace($password,'[TEST_SECRET]').Replace($mongoPassword,'[TEST_SECRET]').Replace($runtimePassword,'[TEST_SECRET]')
    [IO.File]::WriteAllText((Join-Path $scratch ($Name+'.log')),$safe)
    $evidence.Add(@{case=$Name; exit_code=$code; status=$(if($code -eq 0){'PASS'}else{'FAIL'})})
    Write-Output "$Name exit=$code"
    if($code -ne 0){ throw "$Name failed; sanitized log is in $scratch" }
}
function Assert-Owned([string[]]$Arguments,[string]$Expected) {
    $ids=@(& docker @Arguments ps --all --quiet)
    if($LASTEXITCODE -ne 0){ throw 'Cannot inspect test ownership' }
    foreach($id in $ids){
        $labels=(& docker inspect --format '{{json .Config.Labels}}' $id | ConvertFrom-Json)
        if($labels.'com.docker.compose.project' -ne $Expected -or $labels.'etl.portable.owner' -ne 'docker-portable-20261021'){ throw 'Refusing cleanup: ownership mismatch' }
    }
}
function Invoke-Loopback([string]$Uri) {
    $request=[Net.HttpWebRequest]::Create($Uri)
    $request.Proxy=$null
    $request.Timeout=20000
    try { $response=$request.GetResponse() }
    catch {
        $failure=$_.Exception
        while($failure.InnerException){ $failure=$failure.InnerException }
        if($failure -is [Net.WebException] -and $failure.Response){ $response=$failure.Response }
        else { throw }
    }
    try { return [int]$response.StatusCode } finally { $response.Dispose() }
}
try {
    Invoke-Recorded 'compose-config' ($testArgs + @('config','--quiet'))
    Invoke-Recorded 'isolated-db-start' ($testArgs + @('up','-d','--wait','--wait-timeout','120','master-test','source-test','mongo-test'))
    Assert-Owned $testArgs $project
    Invoke-Recorded 'bootstrap-owned-empty-test' ($testArgs + @('run','--rm','--no-deps','-e','ETL_APPROVE_EMPTY_TEST_BOOTSTRAP=docker-portable-20261021','qa','python','/app/deploy/tests/bootstrap_isolated.py'))
    Invoke-Recorded 'native-unit-acceptance' ($testArgs + @('run','--rm','--no-deps','-e','ETL_DOCKER_NATIVE_TEST=1','-e','ETL_REQUIRE_POSTGRES=0','-e','INDEX_EMBEDDING_MODEL_PATH=','-e','INDEX_EMBEDDING_MODEL_ID=','-e','INDEX_EMBEDDING_MODEL_VERSION=','qa','python','-m','pytest','/app/deploy/tests','/app/tests/unit/test_admin_boundary.py','/app/tests/unit/test_search_access.py','/app/tests/unit/test_single_admin_auth.py','/app/tests/unit/test_source_reference.py','/app/tests/unit/test_ocr_readiness.py','/app/tests/unit/test_multiformat.py','/app/tests/unit/test_file_preview.py','-q','--tb=short','--basetemp=/tmp/etl-qa/native','--junitxml=/tmp/etl-qa/native.xml'))
    Invoke-Recorded 'source-ocr-index-search-real' ($testArgs + @('run','--rm','--no-deps','-e','ETL_DOCKER_NATIVE_TEST=1','qa','python','-m','pytest','/app/tests/integration/source_postgres/test_docker_portable.py','-q','--tb=short','--basetemp=/tmp/etl-qa/integration'))
    Invoke-Recorded 'db-readonly-schema-check' ($appArgs + @('--profile','ops','run','--rm','--no-deps','preflight'))
    Invoke-Recorded 'app-five-services' ($appArgs + @('up','-d','--no-build','--wait','--wait-timeout','240','api','frontend','master-sync-worker','source-file-worker','ocr-worker'))
    Assert-Owned $appArgs ($project+'-app')
    foreach($service in @('master-sync-worker','source-file-worker','ocr-worker')){
        Invoke-Recorded ($service+'-heartbeat') ($appArgs + @('exec','-T',$service,'python','/opt/etl/runtime.py','health-worker'))
    }
    $base="http://127.0.0.1:$webPort"
    foreach($route in @('/health','/','/library/deep-link','/web-health')){
        if((Invoke-Loopback ($base+$route)) -ne 200){ throw 'Proxy smoke failed' }
    }
    if((Invoke-Loopback ($base+'/api/v1/resources')) -ne 403){ throw 'Admin boundary unexpectedly allowed proxy' }
    $evidence.Add(@{case='health-static-spa-proxy-boundary';status='PASS';exit_code=0})
    Invoke-Recorded 'local-retention-write' ($appArgs + @('exec','-T','api','python','-c',"from pathlib import Path; Path('/var/lib/etl/local/portable-test-canary').write_text('isolated fixture')"))
    Invoke-Recorded 'sigterm-restart-workers' ($appArgs + @('restart','api','master-sync-worker','source-file-worker','ocr-worker'))
    Invoke-Recorded 'post-restart-health' ($appArgs + @('up','-d','--no-build','--wait','--wait-timeout','240','api','frontend','master-sync-worker','source-file-worker','ocr-worker'))
    if((Invoke-Loopback ($base+'/health')) -ne 200){ throw 'Proxy failed after API restart' }
    Invoke-Recorded 'local-retention-read' ($appArgs + @('exec','-T','api','python','-c',"from pathlib import Path; assert Path('/var/lib/etl/local/portable-test-canary').read_text()=='isolated fixture'"))
    $ownedAppIds=@(& docker @appArgs ps --quiet)
    Invoke-Recorded 'container-resource-snapshot' (@('stats','--no-stream','--format','{{json .}}')+$ownedAppIds)
    Write-Output "PASS: isolated test stack $project; frontend port $webPort"
} catch {
    $evidence.Add(@{case='orchestration';status='FAIL';exit_code=1})
    throw
} finally {
    $evidence | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $scratch 'RESULTS.json') -Encoding ascii
    if(-not $KeepRunning){
        Assert-Owned $appArgs ($project+'-app')
        Invoke-Recorded 'stop-owned-app-no-volume-delete' ($appArgs + @('down','--timeout','420'))
        Assert-Owned $testArgs $project
        Invoke-Recorded 'stop-owned-test-databases' ($testArgs + @('down','--timeout','30'))
    }
    $evidence | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $scratch 'RESULTS.json') -Encoding ascii
    Write-Output "Evidence: $scratch; named test app volumes are retained. No Dev database was selected."
}
