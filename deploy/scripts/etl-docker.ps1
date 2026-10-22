param(
    [ValidateSet('Build','Network','Check','Start','Status','Logs','Stop','Down','DbStart','DbStop','Export','Import','PrepareModel','VerifyModel')]
    [string]$Action = 'Status',
    [string]$EnvFile,
    [string]$Archive,
    [string]$ModelDirectory,
    [string]$ModelId = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
    [string]$ModelRevision = 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42',
    [int]$ModelDimension = 384,
    [string]$NetworkName = 'etl-data',
    [switch]$Workers,
    [switch]$Drive,
    [switch]$RemoteDatabase,
    [ValidateSet('legacy','auth','auth-tls','db-auth','db-auth-tls')][string]$AuthProfile = 'auth',
    [switch]$AcceptModelLicense
)
$ErrorActionPreference = 'Stop'
$deployDir = Split-Path -Parent $PSScriptRoot
if (-not $EnvFile) { $EnvFile = Join-Path $deployDir '.env.docker' }
$composeArgs = @('compose','--env-file',$EnvFile,'-f',(Join-Path $deployDir 'compose.app.yml'))
if ($Drive) { $composeArgs += @('-f',(Join-Path $deployDir 'compose.drive.yml')) }
if ($RemoteDatabase) { $composeArgs += @('-f',(Join-Path $deployDir 'compose.remote-db.yml')) }
if ($AuthProfile -like 'db-auth*') { $composeArgs += @('-f',(Join-Path $deployDir 'compose.db-auth.yml')) }
elseif ($AuthProfile -ne 'legacy') { $composeArgs += @('-f',(Join-Path $deployDir 'compose.auth.yml')) }
if ($AuthProfile -in @('auth-tls','db-auth-tls')) { $composeArgs += @('-f',(Join-Path $deployDir 'compose.auth-tls.yml')) }
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
    Build { Invoke-Docker ($composeArgs + @('build','api','frontend')) }
    Network {
        & docker network inspect $NetworkName *> $null
        if ($LASTEXITCODE -ne 0) { Invoke-Docker @('network','create',$NetworkName) }
    }
    Check { Test-Deployment }
    Start {
        Test-Deployment
        if ($Workers) { Invoke-Docker ($composeArgs + @('--profile','workers','up','-d','--no-build','api','frontend','master-sync-worker','source-file-worker','ocr-worker')) }
        else { Invoke-Docker ($composeArgs + @('up','-d','--no-build','api','frontend')) }
        Write-Output "Application started with profile: $AuthProfile. Inspect health and acceptance report."
    }
    Status { Invoke-Docker ($composeArgs + @('--profile','workers','ps')) }
    Logs { Invoke-Docker ($composeArgs + @('--profile','workers','logs','--tail','100')) }
    Stop { Invoke-Docker ($composeArgs + @('--profile','workers','stop')) }
    Down { Invoke-Docker ($composeArgs + @('--profile','workers','down')) }
    DbStart { Invoke-Docker @('compose','--env-file',$EnvFile,'-f',(Join-Path $deployDir 'compose.database.yml'),'up','-d') }
    DbStop { Invoke-Docker @('compose','--env-file',$EnvFile,'-f',(Join-Path $deployDir 'compose.database.yml'),'stop') }
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
