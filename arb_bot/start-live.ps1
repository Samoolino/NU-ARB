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
    Write-Host "Live spot pilot setup. Credentials are masked and are not written to a file."
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

    $sizeText = Read-Host "Maximum USD notional per engagement (1-25; default 5)"
    $tradeSize = 5.0
    if ($sizeText) {
        $parsedSize = 0.0
        $validSize = [double]::TryParse(
            $sizeText,
            [Globalization.NumberStyles]::AllowDecimalPoint,
            [Globalization.CultureInfo]::InvariantCulture,
            [ref]$parsedSize
        )
        if (-not $validSize -or $parsedSize -lt 1.0 -or $parsedSize -gt 25.0) {
            throw "Trade size must be a number between 1 and 25 USD."
        }
        $tradeSize = $parsedSize
    }

    Set-ProcessSetting "BOT_EXCHANGES" ($venues -join ",")
    Set-ProcessSetting "BOT_MODE" "live"
    Set-ProcessSetting "BOT_CROSS" "1"
    Set-ProcessSetting "BOT_CROSS_LIVE" "1"
    Set-ProcessSetting "BOT_TRADE_SIZE_USD" $tradeSize.ToString([Globalization.CultureInfo]::InvariantCulture)
    Set-ProcessSetting "BOT_MAX_LOSS_USD" "3"
    Set-ProcessSetting "BOT_TARGET_PROFIT_USD" "200"

    foreach ($venue in $venues) {
        $prefix = "BOT_$($venue.ToUpperInvariant())"
        Set-ProcessSetting "$($prefix)_KEY" (Read-MaskedValue "$venue API key")
        Set-ProcessSetting "$($prefix)_SECRET" (Read-MaskedValue "$venue API secret or signing key")
        if ($venue -eq "kucoin") {
            Set-ProcessSetting "$($prefix)_PASSWORD" (Read-MaskedValue "kucoin API passphrase")
        }
    }

    Write-Host ""
    Write-Host "Selected venues: $($venues -join ', ')"
    Write-Host ("Per-engagement notional cap: ${tradeSize} USD; session halt: -3 USD; realized-profit stop: +200 USD.")
    Write-Host "Cross-venue orders are non-atomic; partial fills and unwinds can lose more than the configured halt."
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

    Write-Host "Starting live pilot. Keep this terminal open; press Ctrl+C to stop new order activity."
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
