$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python not found at '$python'. Create .venv and install arb_bot\requirements.txt first."
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

try {
    Set-Location -LiteralPath $PSScriptRoot
    Write-Host "Live CEX pilot setup: $3 starter capital + profit-compounding DCA policy."
    Write-Host "The engine does not average down losing positions. A realized loss halts further live engagements."
    Write-Host "Only Binance, Bybit, and KuCoin currently have permission probes that may qualify for live."
    Write-Host "Use API keys restricted to read + spot trading, with withdrawals/transfers disabled and IP allowlisting enabled."

    $selection = Read-Host "Enter at least two credentialed venues (binance, bybit, kucoin)"
    $venues = @($selection.Split(",") | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
    $supported = @("binance", "bybit", "kucoin")
    $invalid = @($venues | Where-Object { $_ -notin $supported })
    if ($invalid.Count -gt 0) {
        throw "Unsupported live venue(s): $($invalid -join ', '). Allowed: $($supported -join ', ')."
    }
    if ($venues.Count -lt 2 -or @($venues | Select-Object -Unique).Count -ne $venues.Count) {
        throw "Select at least two distinct live candidate venues."
    }

    # Capital is intentionally not user-configurable in live mode.
    # Config.validate() independently enforces the same values server-side.
    Set-ProcessSetting "BOT_EXCHANGES" ($venues -join ",")
    Set-ProcessSetting "BOT_MODE" "live"
    Set-ProcessSetting "BOT_ALLOW_ORDERS" "0"
    Set-ProcessSetting "BOT_CROSS" "1"
    Set-ProcessSetting "BOT_CROSS_LIVE" "1"
    Set-ProcessSetting "BOT_TRADE_SIZE_USD" "3"
    Set-ProcessSetting "BOT_START_CAPITAL_USD" "3"
    Set-ProcessSetting "BOT_STRATEGY_MODE" "profit_dca"
    Set-ProcessSetting "BOT_COMPOUND_PROFITS" "1"
    Set-ProcessSetting "BOT_HALT_ON_REALIZED_LOSS" "1"
    Set-ProcessSetting "BOT_MAX_LOSS_USD" "0"
    Set-ProcessSetting "BOT_TARGET_PROFIT_USD" ""

    foreach ($venue in $venues) {
        $prefix = "BOT_$($venue.ToUpperInvariant())"
        Set-ProcessSetting "$($prefix)_KEY" (Read-MaskedValue "$venue API key")
        if ($venue -eq "binance") {
            $authMode = (Read-Host "Binance auth mode (hmac or ed25519)").Trim().ToLowerInvariant()
            if ($authMode -notin @("hmac", "ed25519")) {
                throw "Binance live startup supports only hmac or ed25519 credentials."
            }
            Set-ProcessSetting "$($prefix)_AUTH_MODE" $authMode
            if ($authMode -eq "ed25519") {
                $keyPath = Read-Host "Path to the Binance Ed25519 PKCS8 PEM private signing key"
                if (-not $keyPath -or -not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
                    throw "A readable Ed25519 private-key file is required."
                }
                $privateKey = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $keyPath).ProviderPath)
                Set-ProcessSetting "$($prefix)_PRIVATE_KEY" $privateKey
                $keyCheck = 'import os; from cryptography.hazmat.primitives.serialization import load_pem_private_key; from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; key=load_pem_private_key(os.environ["BOT_BINANCE_PRIVATE_KEY"].encode("utf-8"), password=None); raise SystemExit(0 if isinstance(key, Ed25519PrivateKey) else 2)'
                & $python -c $keyCheck
                if ($LASTEXITCODE -ne 0) {
                    throw "The supplied Binance key is not a valid Ed25519 PKCS8 PEM private key."
                }
            }
            else {
                Set-ProcessSetting "$($prefix)_SECRET" (Read-MaskedValue "Binance API secret")
            }
        }
        else {
            Set-ProcessSetting "$($prefix)_AUTH_MODE" "hmac"
            Set-ProcessSetting "$($prefix)_SECRET" (Read-MaskedValue "$venue API secret")
        }
        if ($venue -eq "kucoin") {
            Set-ProcessSetting "$($prefix)_PASSWORD" (Read-MaskedValue "kucoin API passphrase")
        }
    }

    Write-Host ""
    Write-Host "Starter capital: $3 USD."
    Write-Host "Next engagement capital = $3 + cumulative realized positive P&L, capped by actual exchange inventory."
    Write-Host "No fixed session-loss target. Any realized live loss halts new engagements."
    Write-Host "Cross-venue orders are non-atomic; modeled positive profit is not a guarantee of realized profit."
    Write-Host ""

    & $python run.py selftest
    if ($LASTEXITCODE -ne 0) {
        throw "Offline self-test failed (exit code $LASTEXITCODE). No live preflight or orders started."
    }

    & $python run.py live-preflight
    if ($LASTEXITCODE -ne 0) {
        throw "Live preflight failed (exit code $LASTEXITCODE). No live orders started."
    }

    $confirmation = Read-Host "If you reviewed all balances/evidence and accept the risks, type START LIVE to begin"
    if ($confirmation -cne "START LIVE") {
        Write-Host "Live run cancelled. No orders were submitted."
        return
    }

    # The separate submission switches stay disabled through setup, self-test,
    # preflight, and the confirmation prompt. Set them only in this process after
    # the operator's final explicit authorization; finally restores prior values.
    Set-ProcessSetting "BOT_ALLOW_ORDERS" "1"

    Write-Host "Starting live $3 profit-compounding pilot. Keep this terminal open; press Ctrl+C to stop new order activity."
    & $python run.py run --headless
    if ($LASTEXITCODE -ne 0) {
        throw "Live process exited with code $LASTEXITCODE."
    }
}
finally {
    foreach ($name in @($changedEnvironment.Keys)) {
        $previousValue = $changedEnvironment[$name]
        [Environment]::SetEnvironmentVariable($name, $previousValue, "Process")
    }
}