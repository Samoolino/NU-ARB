[CmdletBinding()]
param(
 [ValidateSet("audit","scaffold","validate","paper","live-preflight")][string]$Stage="audit",
 [string[]]$Venues=@("binance","bybit","okx","kucoin","gateio","bitget","kraken","coinbase","mexc","htx","bitfinex","cryptocom","coinex","bitstamp","gemini","bingx","lbank","whitebit","bitmart","upbit"),
 [string]$Symbol="BTC/USDT",[double]$Notional=3.0
)
$ErrorActionPreference="Stop"; Set-Location (Split-Path -Parent $PSScriptRoot); $env:PYTHONPATH="$PWDarb_bot"
Write-Host "=== NU-ARB HYBRID CORE ===" -ForegroundColor Cyan
Write-Host "Stage=$Stage Symbol=$Symbol Notional=$Notional"
switch($Stage){
 "audit" { python -m arbx.cli selftest; if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}; python -m arbx.cli probe }
 "scaffold" { python -m compileall arb_bot; if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}; python -c "from arbx.hybrid.registry import VENUE_CATALOG; print('VENUES=',len(VENUE_CATALOG)); print([v.id for v in VENUE_CATALOG])" }
 "validate" { $env:BOT_EXCHANGES=$Venues -join ","; $env:BOT_MODE="paper"; $env:BOT_PREFLIGHT_SYMBOL=$Symbol; python -m arbx.cli preflight }
 "paper" { $env:BOT_EXCHANGES=$Venues -join ","; $env:BOT_MODE="paper"; $env:BOT_PREFLIGHT_SYMBOL=$Symbol; python -m arbx.cli preflight; if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}; python -m arbx.cli run --headless }
 "live-preflight" { $env:BOT_EXCHANGES=$Venues -join ","; $env:BOT_MODE="live"; $env:BOT_CROSS="1"; $env:BOT_CROSS_LIVE="1"; $env:BOT_PREFLIGHT_SYMBOL=$Symbol; Write-Host "READ-ONLY: NO ORDERS OR TRANSFERS" -ForegroundColor Yellow; python -m arbx.cli live-preflight }
}
