param(
    [string]$OutputPath = 'D:\etl\private\source-write-database-url',
    [string]$HostName,
    [string]$DatabaseName,
    [int]$Port = 5432,
    [ValidateSet('require', 'verify-full')]
    [string]$SslMode = 'require'
)

$ErrorActionPreference = 'Stop'

if (Test-Path -LiteralPath $OutputPath -PathType Leaf) {
    throw "Refusing to overwrite existing secret file: $OutputPath"
}

if (-not $HostName) {
    $HostName = Read-Host 'SOURCE Neon hostname'
}
if (-not $DatabaseName) {
    $DatabaseName = Read-Host 'SOURCE database name'
}
if ([string]::IsNullOrWhiteSpace($HostName) -or [string]::IsNullOrWhiteSpace($DatabaseName)) {
    throw 'SOURCE hostname and database name are required.'
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw 'Port must be between 1 and 65535.'
}

$securePassword = Read-Host 'Password for etl_source_writer' -AsSecureString
$passwordPtr = [IntPtr]::Zero
try {
    $passwordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPtr)
    if ([string]::IsNullOrEmpty($password)) {
        throw 'Writer password cannot be empty.'
    }

    $encodedPassword = [Uri]::EscapeDataString($password)
    $dsn = 'postgresql+psycopg://etl_source_writer:{0}@{1}:{2}/{3}?sslmode={4}' -f `
        $encodedPassword, $HostName, $Port, $DatabaseName, $SslMode
    $parent = Split-Path -Parent $OutputPath
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($OutputPath, $dsn, $utf8NoBom)
    Write-Output "Writer DSN secret created: $OutputPath"
    Write-Output 'The DSN value was not printed. Configure the contract separately before enabling write-back.'
}
finally {
    if ($passwordPtr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPtr)
    }
    $password = $null
    $encodedPassword = $null
    $dsn = $null
}
