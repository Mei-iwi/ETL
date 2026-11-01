param(
    [string]$OutputPath = 'D:\etl\private\source-write-contract.json',
    [Parameter(Mandatory = $true)]
    [string]$ApplicationRole,
    [string[]]$AllowedActions = @('update'),
    [string[]]$AllowedActorIds = @(),
    [string[]]$AllowedFields = @('title')
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
$supportedActions = @('create', 'update', 'delete')
$normalizedActions = @($AllowedActions | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
if ($normalizedActions.Count -eq 0 -or @($normalizedActions | Where-Object { $_ -notin $supportedActions }).Count -gt 0) {
    throw 'AllowedActions must contain only create, update or delete.'
}
$normalizedActors = @($AllowedActorIds | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$normalizedFields = @($AllowedFields | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($normalizedFields.Count -eq 0) {
    throw 'AllowedFields must contain at least one explicitly approved SOURCE column.'
}
$requiredFields = @()
if ($normalizedActions -contains 'create') {
    $requiredFields += @('id', 'provider_id', 'resource_type_id', 'created_by', 'title',
        'publication_status', 'is_active', 'created_at', 'updated_at')
}
if ($normalizedActions -contains 'delete') {
    $requiredFields += @('is_active', 'deleted_at', 'deleted_by', 'updated_at')
}
$missingFields = @($requiredFields | Where-Object { $_ -notin $normalizedFields })
if ($missingFields.Count -gt 0) {
    throw "AllowedFields is missing fields required by the selected action(s): $($missingFields -join ', ')"
}

$contract = Get-Content -LiteralPath $examplePath -Raw | ConvertFrom-Json
$contract.allowed_roles = @($ApplicationRole.Trim().ToUpperInvariant())
$contract.allowed_actor_ids = $normalizedActors
$contract.actions = $normalizedActions
$contract.columns = @{}
foreach ($field in $normalizedFields) {
    $contract.columns | Add-Member -NotePropertyName $field -NotePropertyValue $field -Force
}

$parent = Split-Path -Parent $OutputPath
if ($parent) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$json = $contract | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText($OutputPath, $json, $utf8NoBom)
Write-Output "Writer contract created: $OutputPath"
Write-Output "Allowed application role: $($ApplicationRole.Trim().ToUpperInvariant())"
Write-Output "Allowed actions: $($normalizedActions -join ', ')"
Write-Output "Allowed fields: $($normalizedFields -join ', ')"
