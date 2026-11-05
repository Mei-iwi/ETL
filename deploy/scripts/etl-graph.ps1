param(
  [ValidateSet('GraphStart','GraphStop','GraphStatus')][string]$Action = 'GraphStatus',
  [string]$EnvFile = '.env'
)
$ErrorActionPreference = 'Stop'
$graphPython = if (Test-Path -LiteralPath '.venv/Scripts/python.exe') { '.venv/Scripts/python.exe' } else { 'python' }
& $graphPython deploy/scripts/check-neo4j-config.py --env-file $EnvFile
if ($LASTEXITCODE -ne 0) { throw 'GRAPH_RUNTIME_CONFIG_INVALID' }
$graphArgs = @('compose', '-p', 'etl-neo4j', '--env-file', $EnvFile, '-f', 'deploy/compose.neo4j.yml')
$graphRecreate = @()
if (Select-String -LiteralPath $EnvFile -Pattern '^\s*DOCKER_ENV_SECRETS\s*=\s*["'']?true["'']?\s*$' -Quiet) {
  & $graphPython deploy/scripts/materialize-env-secrets.py --env-file $EnvFile --graph-only
  if ($LASTEXITCODE -ne 0) { throw 'GRAPH_ENV_SECRETS_INVALID' }
  $graphArgs += @('-f', 'deploy/compose.neo4j-env.yml')
  $graphRecreate = @('--force-recreate')
}
& docker @graphArgs config --quiet
if ($LASTEXITCODE -ne 0) { throw 'GRAPH_COMPOSE_INVALID' }
switch ($Action) {
  'GraphStart' { & docker @graphArgs up -d --wait @graphRecreate }
  'GraphStop' { & docker @graphArgs stop }
  'GraphStatus' { & docker @graphArgs ps }
}
exit $LASTEXITCODE
