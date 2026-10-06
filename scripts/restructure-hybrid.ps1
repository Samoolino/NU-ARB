[CmdletBinding()]
param(
 [ValidateSet("audit","scaffold","validate","paper","live-preflight","venue-verify")][string]$Stage="audit",
 [string[]]$Venues=@("binance","bybit","okx","kucoin","gateio","bitget","kraken","coinbase","mexc","htx","bitfinex","cryptocom","coinex","bitstamp","gemini","bingx","lbank","whitebit","bitmart","upbit"),
 [string]$Symbol="BTC/USDT",[double]$Notional=3.0
)
$ErrorActionPreference="Stop"; Set-Location (Split-Path -Parent $PSScriptRoot); $env:PYTHONPATH="$PWD\arb_bot"
Write-Host "=== NU-ARB HYBRID CORE ===" -ForegroundColor Cyan
Write-Host "Stage=$Stage Symbol=$Symbol Notional=$Notional"
switch($Stage){
 "audit" { python -m arbx.cli selftest; if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}; python -m arbx.cli probe }
 "scaffold" { python -m compileall arb_bot; if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}; python -c "from arbx.hybrid.registry import VENUE_CATALOG; print('VENUES=',len(VENUE_CATALOG)); print([v.id for v in VENUE_CATALOG])" }
 "validate" { $env:BOT_EXCHANGES = ($Venues -join ","); $env:BOT_MODE="paper"; $env:BOT_PREFLIGHT_SYMBOL=$Symbol; python -m arbx.cli preflight }
 "paper" { $env:BOT_EXCHANGES = ($Venues -join ","); $env:BOT_MODE="paper"; $env:BOT_PREFLIGHT_SYMBOL=$Symbol; python -m arbx.cli preflight; if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}; python -m arbx.cli run --headless }
 "venue-verify" {
   $failed = 0
   foreach($Venue in $Venues){
     Write-Host "=== VERIFY $Venue / $Symbol / $Notional USD ===" -ForegroundColor Yellow
     $env:BOT_EXCHANGES = $Venue
     $env:BOT_MODE="live"
     $env:BOT_HYBRID_LIVE="1"
     $env:BOT_HYBRID_VALIDATION_ONLY="1"
     $env:BOT_PREFLIGHT_SYMBOL=$Symbol
     python -m arbx.cli hybrid-validate $Venue
     if($LASTEXITCODE -ne 0){ $failed++ }
   }
   Remove-Item Env:BOT_HYBRID_VALIDATION_ONLY -ErrorAction SilentlyContinue
   Remove-Item Env:BOT_HYBRID_LIVE -ErrorAction SilentlyContinue
   if($failed -gt 0){ Write-Host "$failed venue(s) failed certification." -ForegroundColor Red; exit 1 }
   Write-Host "All requested venues passed live certification." -ForegroundColor Green
 }
 "live-preflight" { & $PSCommandPath -Stage venue-verify -Venues $Venues -Symbol $Symbol -Notional $Notional }
}
