[CmdletBinding()]
param(
    [string]$Symbol = "BTC/USDT",
    [string[]]$Venues = @("binance", "bybit", "kucoin", "mexc", "htx", "okx", "bitfinex")
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python not found at '$python'. Create .venv and install requirements.txt first."
}

$supported = @("binance", "bybit", "kucoin", "htx", "mexc", "okx", "bitfinex")
$normalized = @($Venues | ForEach-Object { $_.Trim().ToLowerInvariant() })
$invalid = @($normalized | Where-Object { $_ -notin $supported })
if (-not $Symbol -or $Symbol -cnotmatch '^[A-Z0-9]{2,24}/[A-Z0-9]{2,24}$') {
    throw "Symbol must be one exact uppercase BASE/QUOTE pair, for example BTC/USDT."
}
if ($normalized.Count -eq 0 -or $invalid.Count -gt 0 `
    -or @($normalized | Select-Object -Unique).Count -ne $normalized.Count) {
    throw "Select unique permission-probed venues: $($supported -join ', ')."
}

$changedEnvironment = @{}
function Set-ProcessSetting {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value
    )
    if (-not $changedEnvironment.ContainsKey($Name)) {
        $changedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
    }
    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
}

function Read-MaskedValue {
    param([Parameter(Mandatory = $true)][string]$Prompt)

    $secureValue = Read-Host $Prompt -AsSecureString
    if ($secureValue.Length -eq 0) {
        $secureValue.Dispose()
        throw "A non-empty value is required."
    }

    $pointer = [IntPtr]::Zero
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
        $secureValue.Dispose()
    }
}

function Read-BinancePrivateKey {
    param([Parameter(Mandatory = $true)][string]$Prefix)

    $keyPath = Read-Host "Path to the Binance Ed25519 PKCS8 PEM private signing key"
    if (-not $keyPath -or -not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
        throw "A readable Ed25519 private-key file is required."
    }
    $privateKey = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $keyPath).ProviderPath)
    Set-ProcessSetting "$($Prefix)_PRIVATE_KEY" $privateKey
    $keyCheck = 'import os; from cryptography.hazmat.primitives.serialization import load_pem_private_key; from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; key=load_pem_private_key(os.environ["BOT_BINANCE_PRIVATE_KEY"].encode("utf-8"), password=None); raise SystemExit(0 if isinstance(key, Ed25519PrivateKey) else 2)'
    & $python -c $keyCheck
    if ($LASTEXITCODE -ne 0) {
        throw "The supplied Binance key is not a valid Ed25519 PKCS8 PEM private key."
    }
}

try {
    Set-Location -LiteralPath $PSScriptRoot
    Set-ProcessSetting "BOT_MODE" "paper"
    Set-ProcessSetting "BOT_ALLOW_ORDERS" "0"
    Set-ProcessSetting "BOT_PREFLIGHT_SYMBOL" $Symbol

    foreach ($venue in $normalized) {
        $prefix = "BOT_$($venue.ToUpperInvariant())"
        Set-ProcessSetting "$($prefix)_KEY" (Read-MaskedValue "$venue API key")
        if ($venue -eq "binance") {
            $authMode = (Read-Host "Binance auth mode (hmac or ed25519)").Trim().ToLowerInvariant()
            if ($authMode -notin @("hmac", "ed25519")) {
                throw "Binance feed validation supports only hmac or ed25519 credentials."
            }
            Set-ProcessSetting "$($prefix)_AUTH_MODE" $authMode
            if ($authMode -eq "ed25519") {
                Read-BinancePrivateKey -Prefix $prefix
            }
            else {
                Set-ProcessSetting "$($prefix)_SECRET" (Read-MaskedValue "Binance API secret")
            }
        }
        else {
            Set-ProcessSetting "$($prefix)_AUTH_MODE" "hmac"
            Set-ProcessSetting "$($prefix)_SECRET" (Read-MaskedValue "$venue API secret")
        }
        if ($venue -in @("kucoin", "okx")) {
            Set-ProcessSetting "$($prefix)_PASSWORD" (Read-MaskedValue "$venue API passphrase")
        }
    }

    Write-Host "Validating exact pair $Symbol on: $($normalized -join ', ')"
    Write-Host "Requires complete permission evidence and valid REST + WebSocket books for the same pair."
    Write-Host "This is read-only; transient feed errors retry with backoff. Ctrl+C preserves the report."
    & $python run.py authenticated-feed-validation $Symbol @normalized
    if ($LASTEXITCODE -ne 0) {
        throw "Strict authenticated scanner validation ended with blockers (exit code $LASTEXITCODE). See diagnostics\authenticated-feed-validation-latest.json."
    }
}
finally {
    foreach ($name in @($changedEnvironment.Keys)) {
        [Environment]::SetEnvironmentVariable($name, $changedEnvironment[$name], "Process")
    }
}
