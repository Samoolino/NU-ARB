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
    Write-Host "Live spot pilot setup — $3 starter capital."
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

    $starterCapital = 3.0
    Set-ProcessSetting "BOT_START_CAPITAL_USD" $starterCapital.ToString([Globalization.CultureInfo]::InvariantCulture)
    Set-ProcessSetting "BOT_TRADE_SIZE_USD" $starterCapital.ToString([Globalization.CultureInfo]::InvariantCulture)
    Set-ProcessSetting "BOT_EXCHANGES" ($venues -join ",")
    Set-ProcessSetting "BOT_MODE" "live"
    Set-ProcessSetting "BOT_CROSS" "1"
    Set-ProcessSetting "BOT_CROSS_LIVE" "1"

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
    Write-Host "Starter capital: $3 USD; no separate session-loss ceiling; no fixed profit target."
    Write-Host "Actual exposure remains bounded by available exchange inventory and the modeled-profit/execution gates."
    Write-Host "Cross-venue orders are non-atomic; partial fills and unwinds can still lose capital."
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

    Write-Host "Starting live run with $3 starter capital. Keep this terminal open; press Ctrl+C to stop new order activity."
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
