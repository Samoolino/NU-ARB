[CmdletBinding()]
param(
  [string]$Symbols = "BTC/USDT",
  [int]$WebhookPort = 8765,
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

$Python = Join-Path (Get-Location) ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "NU-ARB virtual environment Python not found: $Python"
}

if (-not $SkipInstall) {
    & $Python -m pip install -r ".\arb_bot\requirements.txt"

    if ($LASTEXITCODE -ne 0) {
        throw "Python dependency installation failed."
    }
}

$env:PYTHONPATH = Join-Path (Get-Location) "arb_bot"

$env:BOT_OPPORTUNITY_SYMBOLS = $Symbols
$env:BOT_OPPORTUNITY_WEBHOOK_PORT = "$WebhookPort"
$env:BOT_MODE = "paper"
$env:BOT_HYBRID_LIVE = "0"
$env:BOT_CROSS_LIVE = "0"
$env:BOT_HYBRID_VALIDATION_ONLY = "1"

Write-Host ""
Write-Host "NU-ARB persistent opportunity finder" -ForegroundColor Cyan
Write-Host "Mode: PAPER / SCANNER ONLY"
Write-Host "Sources: REST + WS orderbooks + authenticated balances + latency observations"
Write-Host "No orders are submitted by this process."
Write-Host "Symbols: $Symbols"
Write-Host "Webhook: 127.0.0.1:$WebhookPort"
Write-Host "Python: $Python"
Write-Host "PYTHONPATH: $env:PYTHONPATH"
Write-Host ""

& $Python -m arbx.hybrid.opportunity_finder

if ($LASTEXITCODE -ne 0) {
    throw "Opportunity finder exited with code $LASTEXITCODE."
}
