[CmdletBinding()]
param(
    [string]$Symbol = "BTC/USDT"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project virtual environment not found at $python. Install arb_bot\requirements.txt first."
}

$env:BOT_MODE = "paper"
$env:BOT_ALLOW_ORDERS = "0"
$env:ARBX_LIVE_TRADING_ENABLED = "0"
$env:BOT_CROSS_LIVE = "0"
$env:BOT_TRIANGULAR_LIVE = "0"
$env:BOT_EXCHANGES = "binance,mexc,kucoin,htx"
$env:BOT_CROSS = "1"
$env:BOT_PREFLIGHT_SYMBOL = $Symbol

Push-Location $PSScriptRoot
try {
    Write-Host "Checking public REST and order-book WebSocket feeds for Binance, MEXC, KuCoin, and HTX."
    Write-Host "Execution remains PAPER; live order submission is explicitly disabled."
    & $python run.py public-feed-check
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Host "Starting paper engine on verified live market data. Press Ctrl+C to stop."
    & $python run.py --headless
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
