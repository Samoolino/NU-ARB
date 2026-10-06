[CmdletBinding()]
param(
  [string]$Symbols = "BTC/USDT",
  [int]$WebhookPort = 8765,
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not $SkipInstall) {
  python -m pip install -r requirements.txt
}

$env:BOT_OPPORTUNITY_SYMBOLS = $Symbols
$env:BOT_OPPORTUNITY_WEBHOOK_PORT = "$WebhookPort"
$env:BOT_MODE = "paper"

Write-Host "NU-ARB persistent opportunity finder"
Write-Host "Sources: CCXT Pro WS + REST books + authenticated REST balances + latency webhooks"
Write-Host "Scanner-only: no orders are submitted by this process."
Write-Host "Symbols: $Symbols"
Write-Host "Webhook: 127.0.0.1:$WebhookPort"

python -m arbx.hybrid.opportunity_finder
