param(
    [string]$OutputPath = 'D:\etl\private\source-write-contract.json',
    [Parameter(Mandatory = $true)]
    [string]$ApplicationRole
)

$ErrorActionPreference = 'Stop'
$examplePath = Join-Path (Split-Path -Parent $PSScriptRoot) 'config/source-write-contract.example.json'

if (Test-Path -LiteralPath $OutputPath -PathType Leaf) {
    throw "Refusing to overwrite existing contract file: $OutputPath"
}
if (-not (Test-Path -LiteralPath $examplePath -PathType Leaf)) {
    throw "Contract example is missing: $examplePath"
}
if ([string]::IsNullOrWhiteSpace($ApplicationRole)) {
    throw 'ApplicationRole is required; use the exact approved API actor role.'
}

$contract = Get-Content -LiteralPath $examplePath -Raw | ConvertFrom-Json
$contract.allowed_roles = @($ApplicationRole.Trim().ToUpperInvariant())
$contract.allowed_actor_ids = @()
$contract.actions = @('update')

$parent = Split-Path -Parent $OutputPath
if ($parent) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$json = $contract | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText($OutputPath, $json, $utf8NoBom)
Write-Output "Writer contract created: $OutputPath"
Write-Output "Allowed application role: $($ApplicationRole.Trim().ToUpperInvariant())"
Write-Output 'Only update/title is enabled; CREATE and DELETE remain disabled.'
