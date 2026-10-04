param(
    [switch]$EnableLive
)

$ErrorActionPreference = "Stop"
$repositoryRoot = $PSScriptRoot
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python not found at '$python'. Create .venv and install arb_bot\requirements.txt first."
}

$localUri = "http://127.0.0.1:8765"
try {
    $health = Invoke-RestMethod -Uri "$localUri/healthz" -TimeoutSec 2
    $runtime = Invoke-RestMethod -Uri "$localUri/api/control?path=%2Fapi%2Fv1%2Fruntime" -TimeoutSec 2
    if ($health.status -eq "ready" -and
        $runtime.PSObject.Properties.Name -contains "liveTradingEnabled" -and
        $runtime.PSObject.Properties.Name -contains "enginePhase") {
        Write-Host "ARBX local UI is already running at $localUri"
        Write-Host "Live enabled: $($runtime.liveTradingEnabled); engine phase: $($runtime.enginePhase)"
        Start-Process $localUri
        return
    }
}
catch {
    # No matching local ARBX service is available; start one below.
}

$dataDirectory = Join-Path $env:LOCALAPPDATA "ARBX"
$databasePath = Join-Path $dataDirectory "local-control.sqlite3"
$encryptionKeyPath = Join-Path $dataDirectory "credential-encryption-key.dpapi"
New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null

if (Test-Path -LiteralPath $encryptionKeyPath -PathType Leaf) {
    $protectedKey = Get-Content -LiteralPath $encryptionKeyPath -Raw
    $secureKey = ConvertTo-SecureString $protectedKey
    $keyPointer = [IntPtr]::Zero
    try {
        $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        $encryptionKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    }
    finally {
        if ($keyPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
        }
        $secureKey.Dispose()
    }
}
else {
    $randomBytes = New-Object byte[] 32
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($randomBytes)
        $encryptionKey = [BitConverter]::ToString($randomBytes).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $random.Dispose()
        [Array]::Clear($randomBytes, 0, $randomBytes.Length)
    }
    $secureKey = ConvertTo-SecureString $encryptionKey -AsPlainText -Force
    ConvertFrom-SecureString $secureKey | Set-Content -LiteralPath $encryptionKeyPath -NoNewline
    $secureKey.Dispose()
}

if ($encryptionKey -notmatch '^[0-9a-fA-F]{64}$') {
    throw "The locally protected credential encryption key could not be read. Do not delete or replace it while saved credentials are needed."
}

$proxyBytes = New-Object byte[] 32
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $random.GetBytes($proxyBytes)
    $proxyToken = [BitConverter]::ToString($proxyBytes).Replace("-", "").ToLowerInvariant()
}
finally {
    $random.Dispose()
    [Array]::Clear($proxyBytes, 0, $proxyBytes.Length)
}

$previousEnvironment = @{}
function Set-LocalEnvironment {
    param([string]$Name, [string]$Value)
    if (-not $previousEnvironment.ContainsKey($Name)) {
        $previousEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
    }
    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
}

try {
    Set-LocalEnvironment "ENGINE_PROXY_TOKEN" $proxyToken
    Set-LocalEnvironment "CREDENTIAL_ENCRYPTION_KEY" $encryptionKey
    Set-LocalEnvironment "ARBX_APP_DB" $databasePath
    Set-LocalEnvironment "APP_SECURE_COOKIE" "0"
    Set-LocalEnvironment "ARBX_LIVE_TRADING_ENABLED" "0"

    if ($EnableLive) {
        Write-Warning "Enabling the browser's live-order control. REAL orders can use REAL exchange funds."
        Write-Warning "The 3 USD session halt does not guarantee a maximum loss; partial fills/unwinds may lose more."
        $confirmation = Read-Host "Type ENABLE LOCAL LIVE UI to allow live-order requests in this local process"
        if ($confirmation -cne "ENABLE LOCAL LIVE UI") {
            throw "Live mode was not enabled. Starting the local UI in read-only/paper-capable mode."
        }
        Set-LocalEnvironment "ARBX_LIVE_TRADING_ENABLED" "1"
    }

    Set-Location -LiteralPath (Join-Path $repositoryRoot "arb_bot")
    $arguments = @(
        "-m", "uvicorn", "arbx.local_host:app",
        "--app-dir", ".",
        "--host", "127.0.0.1",
        "--port", "8765"
    )
    Write-Host "ARBX local account UI: $localUri"
    Write-Host "Bound to loopback only; credentials are encrypted in $databasePath."
    if (-not $EnableLive) {
        Write-Host "Live order control is disabled. Use -EnableLive only after review and venue verification."
    }
    Write-Host "After Uvicorn reports startup complete, open the URL above in your browser. Press Ctrl+C to stop."
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Local host exited with code $LASTEXITCODE."
    }
}
finally {
    foreach ($name in @($previousEnvironment.Keys)) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
    }
    $encryptionKey = $null
    $proxyToken = $null
}
