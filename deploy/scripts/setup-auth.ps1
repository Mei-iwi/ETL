param(
    [Parameter(Mandatory=$true)][string]$Directory,
    [Parameter(Mandatory=$true)][string]$AdminEmail,
    [string]$Image='etl-portable-backend:local'
)
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..')).TrimEnd('\','/')
$target=[IO.Path]::GetFullPath($Directory).TrimEnd('\','/')
if($target.Equals($repo,[StringComparison]::OrdinalIgnoreCase) -or $target.StartsWith($repo+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Keep authentication secrets outside Git workspace' }
if($AdminEmail -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$' -or $AdminEmail.EndsWith('.invalid')) { throw 'A real admin email is required' }
New-Item -ItemType Directory -Path $target -Force | Out-Null
& docker run --rm -it --user 10001:10001 --mount "type=bind,source=$target,target=/output" --entrypoint python $Image -m data_sync_etl.security.cli init --directory /output
if($LASTEXITCODE -ne 0) { throw 'Auth secret initialization failed; existing files are never overwritten' }
$portable=$target.Replace('\','/')
Write-Output @"
# Add to docker.env alongside deployment paths; this output contains no secret values.
AUTH_MODE=single_admin
AUTH_APP_ENVIRONMENT=production
ADMIN_ENABLED=true
AUTH_DEV_ADMIN_BYPASS=false
AUTH_PUBLIC_ENABLED=false
AUTH_DEPLOYMENT_PROFILE=localhost
AUTH_COOKIE_SECURE=false
AUTH_ORIGIN=http://127.0.0.1:8080
ADMIN_LOGIN_EMAIL=$AdminEmail
ADMIN_PASSWORD_HASH_SECRET_FILE=$portable/admin_password_hash
SESSION_SIGNING_KEY_SECRET_FILE=$portable/session_signing_key
AUTH_PROXY_KEY_SECRET_FILE=$portable/auth_proxy_key
"@
