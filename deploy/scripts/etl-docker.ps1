param(
    [ValidateSet('Build','Network','Check','Start','Status','Logs','Stop','Down','DbStart','DbStop','Export','Import','PrepareModel','VerifyModel')]
    [string]$Action = 'Status',
    [string]$EnvFile,
    [string]$Archive,
    [string]$ModelDirectory,
    [string]$ModelId = 'BAAI/bge-m3',
    [string]$ModelRevision = '5617a9f61b028005a4858fdac845db406aefb181',
    [int]$ModelDimension = 1024,
    [string]$NetworkName = 'etl-data',
    [switch]$Workers,
    [switch]$Drive,
    [switch]$Media,
    [switch]$RemoteDatabase,
    [switch]$SourceNeon,
    [switch]$Graph,
    [switch]$GraphServices,
    [ValidateSet('legacy','auth','auth-tls','db-auth','db-auth-tls')][string]$AuthProfile = 'auth',
    [switch]$AcceptModelLicense
)
$ErrorActionPreference = 'Stop'
if ($GraphServices) { $Graph = $true }
if ($RemoteDatabase -and $SourceNeon) { throw 'SourceNeon and RemoteDatabase are mutually exclusive.' }
$deployDir = Split-Path -Parent $PSScriptRoot
if (-not $EnvFile) { $EnvFile = $env:ETL_DOCKER_ENV_FILE }
if (-not $EnvFile) {
    $EnvFile = Join-Path (Split-Path -Parent $deployDir) 'docker.env'
    if (-not (Test-Path -LiteralPath $EnvFile)) { $EnvFile = Join-Path $deployDir '.env.docker' }
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) { throw 'Docker env file missing. Copy deploy/docker.env.example to docker.env and configure it.' }
$EnvFile = (Resolve-Path -LiteralPath $EnvFile).Path
$databaseMode = 'same-host'
$mediaEnabled = 'false'
$driveEnabled = 'false'
$sourceWriteOverrideFile = $null
$envSecretsEnabled = $false
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ($line -match '^\s*DOCKER_ENV_SECRETS\s*=') {
        if ($line -notmatch '^\s*DOCKER_ENV_SECRETS\s*=\s*["'']?(true|false)["'']?\s*(?:#.*)?$') { throw 'Invalid DOCKER_ENV_SECRETS.' }
        $envSecretsEnabled = $Matches[1] -eq 'true'
    }
    if ($line -match '^\s*MEDIA_ENABLED\s*=') {
        if ($line -notmatch '^\s*MEDIA_ENABLED\s*=\s*["'']?(true|false)["'']?\s*(?:#.*)?$') { throw 'Invalid MEDIA_ENABLED in Docker env file.' }
        $mediaEnabled = $Matches[1]
    }
    if ($line -match '^\s*DATABASE_MODE\s*=') {
        if ($line -notmatch '^\s*DATABASE_MODE\s*=\s*["'']?(same-host|remote|source-neon|neon-atlas|neon-native-mongo)["'']?\s*(?:#.*)?$') { throw 'Invalid DATABASE_MODE in Docker env file.' }
        $databaseMode = $Matches[1]
    }
    if ($line -match '^\s*SOURCE_DRIVE_ENABLED\s*=') {
        if ($line -notmatch '^\s*SOURCE_DRIVE_ENABLED\s*=\s*["'']?(true|false)["'']?\s*(?:#.*)?$') { throw 'Invalid SOURCE_DRIVE_ENABLED in Docker env file.' }
        $driveEnabled = $Matches[1]
    }
    if ($line -match '^\s*SOURCE_WRITE_COMPOSE_OVERRIDE_FILE\s*=') {
        if ($line -match '^\s*SOURCE_WRITE_COMPOSE_OVERRIDE_FILE\s*=\s*["'']?([^"'']+)["'']?\s*(?:#.*)?$') {
            $sourceWriteOverrideFile = $Matches[1].Trim()
        }
        else { throw 'Invalid SOURCE_WRITE_COMPOSE_OVERRIDE_FILE in Docker env file.' }
    }
}
if ($env:DATABASE_MODE) { $databaseMode = $env:DATABASE_MODE }
if ($databaseMode -notin @('same-host','remote','source-neon','neon-atlas','neon-native-mongo')) { throw 'Invalid DATABASE_MODE.' }
if ($databaseMode -eq 'neon-native-mongo' -and ($RemoteDatabase -or $SourceNeon)) { throw 'Database switch conflicts with native Mongo profile.' }
if ($databaseMode -eq 'neon-atlas' -and ($RemoteDatabase -or $SourceNeon)) { throw 'Database switch conflicts with neon-atlas profile.' }
if (($RemoteDatabase -and $databaseMode -eq 'source-neon') -or ($SourceNeon -and $databaseMode -eq 'remote')) { throw 'Database switch conflicts with DATABASE_MODE.' }
if ($databaseMode -eq 'source-neon') { $SourceNeon = $true }
if ($databaseMode -eq 'remote') { $RemoteDatabase = $true }
if ($env:SOURCE_DRIVE_ENABLED) {
    if ($env:SOURCE_DRIVE_ENABLED -cnotin @('true','false')) { throw 'Invalid SOURCE_DRIVE_ENABLED environment override.' }
    $driveEnabled = $env:SOURCE_DRIVE_ENABLED
}
$Drive = $Drive -or ($driveEnabled -eq 'true')
Write-Output "Docker env: $EnvFile"
Write-Output "Database profile: $databaseMode"
$composeArgs = @('compose','--env-file',$EnvFile,'-f',(Join-Path $deployDir 'compose.app.yml'))
if ($env:MEDIA_ENABLED) {
    if ($env:MEDIA_ENABLED -cnotin @('true','false')) { throw 'Invalid MEDIA_ENABLED.' }
    $mediaEnabled = $env:MEDIA_ENABLED
}
if ($Media -or $mediaEnabled -eq 'true') { $composeArgs += @('-f',(Join-Path $deployDir 'compose.media.yml')) }
if ($Drive) { $composeArgs += @('-f',(Join-Path $deployDir 'compose.drive.yml')) }
if ($RemoteDatabase) { $composeArgs += @('-f',(Join-Path $deployDir 'compose.remote-db.yml')) }
if ($SourceNeon) { $composeArgs += @('-f',(Join-Path $deployDir 'compose.source-neon.yml')) }
if ($databaseMode -eq 'neon-atlas') { $composeArgs += @('-f',(Join-Path $deployDir 'compose.neon-atlas.yml')) }
if ($databaseMode -eq 'neon-native-mongo') { $composeArgs += @('-f',(Join-Path $deployDir 'compose.neon-native-mongo.yml')) }
if ($sourceWriteOverrideFile) {
    if (-not (Test-Path -LiteralPath $sourceWriteOverrideFile -PathType Leaf)) {
        throw 'SOURCE_WRITE_COMPOSE_OVERRIDE_FILE does not exist.'
    }
    $composeArgs += @('-f',(Resolve-Path -LiteralPath $sourceWriteOverrideFile).Path)
}
if ($AuthProfile -like 'db-auth*') { $composeArgs += @('-f',(Join-Path $deployDir 'compose.db-auth.yml')) }
elseif ($AuthProfile -ne 'legacy') { $composeArgs += @('-f',(Join-Path $deployDir 'compose.auth.yml')) }
if ($AuthProfile -in @('auth-tls','db-auth-tls')) { $composeArgs += @('-f',(Join-Path $deployDir 'compose.auth-tls.yml')) }
if ($Graph) { $composeArgs += @('-f',(Join-Path $deployDir 'compose.graph-app.yml')) }
if ($GraphServices) { $composeArgs += @('-f',(Join-Path $deployDir 'compose.graph-workers.yml'),'--profile','graph-services') }
if ($envSecretsEnabled) { $composeArgs += @('-f',(Join-Path $deployDir 'compose.env-secrets.yml')) }
if ($envSecretsEnabled -and $Action -in @('Build','Check','Start')) {
    $envPython = Join-Path (Split-Path -Parent $deployDir) '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $envPython)) { $envPython = 'python' }
    & $envPython (Join-Path $PSScriptRoot 'materialize-env-secrets.py') --env-file $EnvFile
    if ($LASTEXITCODE -ne 0) { throw 'Private env runtime secret preparation failed.' }
}
function Invoke-Docker([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Docker operation failed; inspect sanitized service status.' }
}
function Test-Deployment {
    Invoke-Docker ($composeArgs + @('--profile','workers','config','--quiet'))
    if ($AuthProfile -ne 'legacy') {
        $configuration = & docker @composeArgs --profile workers config --format json
        if ($LASTEXITCODE -ne 0) { throw 'Invalid Compose configuration' }
        $configuration | & docker @composeArgs run --rm --no-deps -T --entrypoint python api /opt/etl/auth_deployment.py
        if ($LASTEXITCODE -ne 0) { throw 'Authentication deployment boundary rejected' }
        Invoke-Docker ($composeArgs + @('run','--rm','--no-deps','api','check-auth'))
    }
    Invoke-Docker ($composeArgs + @('run','--rm','--no-deps','--entrypoint','python','api','/opt/etl/model.py','verify'))
    Invoke-Docker ($composeArgs + @('--profile','ops','run','--rm','--no-deps','preflight'))
}
switch ($Action) {
    Build {
        $buildServices = @('api','frontend')
        if ($GraphServices) { $buildServices += @('users-refresh-worker','graph-worker') }
        Invoke-Docker ($composeArgs + @('build') + $buildServices)
    }
    Network {
        & docker network inspect $NetworkName *> $null
        if ($LASTEXITCODE -ne 0) { Invoke-Docker @('network','create',$NetworkName) }
    }
    Check { Test-Deployment }
    Start {
        Test-Deployment
        $recreateArgs = @()
        if ($envSecretsEnabled) { $recreateArgs = @('--force-recreate') }
        if ($Workers) { Invoke-Docker ($composeArgs + @('--profile','workers','up','-d','--no-build') + $recreateArgs + @('api','frontend','master-sync-worker','source-file-worker','ocr-worker','reconciliation-worker')) }
        else { Invoke-Docker ($composeArgs + @('up','-d','--no-build') + $recreateArgs + @('api','frontend')) }
        if ($GraphServices) { Invoke-Docker ($composeArgs + @('up','-d','--no-build','--no-deps') + $recreateArgs + @('users-refresh-worker','graph-worker')) }
        Write-Output "Application started with profile: $AuthProfile. Inspect health and acceptance report."
    }
    Status { Invoke-Docker ($composeArgs + @('--profile','workers','ps')) }
    Logs { Invoke-Docker ($composeArgs + @('--profile','workers','logs','--tail','100')) }
    Stop { Invoke-Docker ($composeArgs + @('--profile','workers','stop')) }
    Down { Invoke-Docker ($composeArgs + @('--profile','workers','down')) }
    DbStart { Invoke-Docker @('compose','--env-file',$EnvFile,'-f',(Join-Path $deployDir 'compose.master-only.yml'),'up','-d','master-postgres') }
    DbStop { Invoke-Docker @('compose','--env-file',$EnvFile,'-f',(Join-Path $deployDir 'compose.master-only.yml'),'stop','master-postgres') }
    Export {
        if (-not $Archive) { throw 'Archive path required' }
        if ((Test-Path -LiteralPath $Archive) -or (Test-Path -LiteralPath ($Archive+'.sha256'))) { throw 'Archive output already exists' }
        $images = @(& docker @composeArgs config --images | Sort-Object -Unique)
        if ($LASTEXITCODE -ne 0 -or $images.Count -lt 2) { throw 'Image inventory incomplete' }
        Invoke-Docker (@('image','save','-o',$Archive) + $images)
        (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() | Set-Content -LiteralPath ($Archive + '.sha256') -Encoding ascii
    }
    Import {
        if (-not $Archive) { throw 'Archive path required' }
        $expected = (Get-Content -LiteralPath ($Archive + '.sha256')).Trim().Split(' ')[0]
        if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash -ne $expected) { throw 'Archive checksum mismatch' }
        Invoke-Docker @('image','load','-i',$Archive)
    }
    VerifyModel { Invoke-Docker ($composeArgs + @('run','--rm','--no-deps','--entrypoint','python','api','/opt/etl/model.py','encode')) }
    PrepareModel {
        if (-not $ModelDirectory -or -not $AcceptModelLicense) { throw 'ModelDirectory and explicit AcceptModelLicense required' }
        New-Item -ItemType Directory -Path $ModelDirectory -Force | Out-Null
        $resolved = (Resolve-Path -LiteralPath $ModelDirectory).Path
        $image = if ($env:ETL_MODEL_TOOL_IMAGE) { $env:ETL_MODEL_TOOL_IMAGE } else { 'etl-portable-backend:local' }
        Invoke-Docker @('run','--rm','--tmpfs','/run/etl:rw,uid=10001,gid=10001,mode=0700','--mount',"type=bind,source=$resolved,target=/model",'--entrypoint','python',$image,'/opt/etl/model.py','prepare','--directory','/model','--manifest','/model/etl-model-manifest.json','--model-id',$ModelId,'--revision',$ModelRevision,'--dimension',"$ModelDimension",'--accept-model-license')
    }
}
