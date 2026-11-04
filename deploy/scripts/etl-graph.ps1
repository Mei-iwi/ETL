param(
  [ValidateSet('GraphStart','GraphStop','GraphStatus')][string]$Action = 'GraphStatus',
  [string]$EnvFile = '.env'
)
$ErrorActionPreference = 'Stop'
$graphArgs = @('compose', '-p', 'etl-neo4j', '--env-file', $EnvFile, '-f', 'deploy/compose.neo4j.yml')
& docker @graphArgs config --quiet
if ($LASTEXITCODE -ne 0) { throw 'GRAPH_COMPOSE_INVALID' }
switch ($Action) {
  'GraphStart' { & docker @graphArgs up -d --wait }
  'GraphStop' { & docker @graphArgs stop }
  'GraphStatus' { & docker @graphArgs ps }
}
exit $LASTEXITCODE
