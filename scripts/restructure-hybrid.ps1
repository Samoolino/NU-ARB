[CmdletBinding()]
param(
 [ValidateSet("audit","scaffold","architecture","validate","paper","venue-verify","live-preflight")][string]$Stage="audit",
 [string[]]$Venues=@("binance","bybit","okx","kucoin","gateio","bitget","kraken","coinbase","mexc","htx","bitfinex","cryptocom","coinex","bitstamp","gemini","bingx","lbank","whitebit","bitmart","upbit"),
 [string]$Symbol="BTC/USDT",
 [double]$Notional=3.0,
 [ValidateSet("ccxt_pro","ccxt","native")][string]$Adapter="ccxt_pro",
 [switch]$SkipInstall
)

$ErrorActionPreference="Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH="$PWD\arb_bot"

if(-not $SkipInstall -and (Test-Path "requirements.txt")){
  python -m pip install -r requirements.txt
  if($LASTEXITCODE -ne 0){ exit $LASTEXITCODE }
}

Write-Host "=== NU-ARB ADAPTER-CORE ===" -ForegroundColor Cyan
Write-Host "Stage=$Stage Adapter=$Adapter Symbol=$Symbol Notional=$Notional"

function Invoke-PythonChecked([string]$File, [string[]]$Args=@()){
  & python $File @Args
  if($LASTEXITCODE -ne 0){ exit $LASTEXITCODE }
}

switch($Stage){
 "audit" {
   Invoke-PythonChecked "-m" @("arbx.cli","selftest")
   Invoke-PythonChecked "-m" @("compileall","arb_bot")
   Invoke-PythonChecked "-m" @("arbx.cli","hybrid-catalog")
   Invoke-PythonChecked "-m" @("arbx.cli","probe")
 }
 "scaffold" {
   Invoke-PythonChecked "-m" @("compileall","arb_bot")
   Invoke-PythonChecked "-c" @("from arbx.hybrid.registry import VENUE_CATALOG; print('VENUES=',len(VENUE_CATALOG)); print([v.id for v in VENUE_CATALOG])")
 }
 "architecture" {
   Invoke-PythonChecked "-m" @("compileall","arb_bot")
   Invoke-PythonChecked "-m" @("pytest","tests/test_hybrid_engine.py","-q")
   Invoke-PythonChecked "-c" @("from arbx.hybrid.contracts import VenueCapabilities, ExecutionRequirements; from arbx.hybrid.router import validate_route; print('ADAPTER CONTRACT OK'); print(validate_route({'a':VenueCapabilities(spot=True,websocket=True,user_stream=True,limit_orders=True,ioc=True)},('a',),ExecutionRequirements(require_ioc=True)))")
 }
 "validate" {
   $env:BOT_EXCHANGES=($Venues -join ",")
   $env:BOT_MODE="paper"
   $env:BOT_PREFLIGHT_SYMBOL=$Symbol
   $env:BOT_ADAPTER=$Adapter
   Invoke-PythonChecked "-m" @("arbx.cli","preflight")
 }
 "paper" {
   $env:BOT_EXCHANGES=($Venues -join ",")
   $env:BOT_MODE="paper"
   $env:BOT_PREFLIGHT_SYMBOL=$Symbol
   $env:BOT_ADAPTER=$Adapter
   Invoke-PythonChecked "-m" @("arbx.cli","preflight")
   Invoke-PythonChecked "-m" @("arbx.cli","run","--headless")
 }
 "venue-verify" {
   $failed=0
   foreach($Venue in $Venues){
     Write-Host "=== VERIFY $Venue / $Adapter / $Symbol / $Notional USD ===" -ForegroundColor Yellow
     $env:BOT_EXCHANGES=$Venue
     $env:BOT_MODE="live"
     $env:BOT_HYBRID_LIVE="1"
     $env:BOT_HYBRID_VALIDATION_ONLY="1"
     $env:BOT_PREFLIGHT_SYMBOL=$Symbol
     $env:BOT_PREFLIGHT_NOTIONAL_USD=$Notional
     $env:BOT_ADAPTER=$Adapter
     $envName="BOT_" + $Venue.ToUpper() + "_ADAPTER"
     Set-Item -Path ("Env:" + $envName) -Value $Adapter
     python -m arbx.cli hybrid-validate $Venue
     if($LASTEXITCODE -ne 0){ $failed++ }
   }
   Remove-Item Env:BOT_HYBRID_VALIDATION_ONLY -ErrorAction SilentlyContinue
   Remove-Item Env:BOT_HYBRID_LIVE -ErrorAction SilentlyContinue
   Remove-Item Env:BOT_ADAPTER -ErrorAction SilentlyContinue
   if($failed -gt 0){
     Write-Host "$failed venue(s) failed certification." -ForegroundColor Red
     exit 1
   }
   Write-Host "All requested venues passed live certification." -ForegroundColor Green
 }
 "live-preflight" {
   & $PSCommandPath -Stage venue-verify -Venues $Venues -Symbol $Symbol -Notional $Notional -Adapter $Adapter -SkipInstall
   if($LASTEXITCODE -ne 0){ exit $LASTEXITCODE }

   $env:BOT_MODE="live"
   $env:BOT_HYBRID_LIVE="0"
   $env:BOT_HYBRID_VALIDATION_ONLY="0"
   $env:BOT_CROSS="1"
   $env:BOT_CROSS_LIVE="1"
   $env:BOT_STRATEGY_MODE="profit_dca"
   $env:BOT_COMPOUND_PROFITS="1"
   $env:BOT_HALT_ON_REALIZED_LOSS="1"
   $env:BOT_PREFLIGHT_SYMBOL=$Symbol
   $env:BOT_PREFLIGHT_NOTIONAL_USD=$Notional
   $env:BOT_ADAPTER=$Adapter

   Invoke-PythonChecked "-m" @("arbx.cli","live-preflight")
 }
}
