[CmdletBinding()]
param(
  [ValidateSet("audit","architecture","test","paper","venue-verify","live-preflight","live","all")]
  [string]$Stage = "all",
  [string[]]$Venues = @("binance","bybit","okx"),
  [string]$Symbol = "BTC/USDT",
  [double]$Notional = 3.00,
  [ValidateSet("ccxt_pro","ccxt","native")]
  [string]$Adapter = "ccxt_pro",
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = Join-Path $root "arb_bot"

function Step([string]$Name) {
  Write-Host ""
  Write-Host "=== NU-ARB: $Name ===" -ForegroundColor Cyan
}

function Run([string]$Command, [string[]]$Args) {
  Write-Host "> $Command $($Args -join ' ')" -ForegroundColor DarkGray
  & $Command @Args
  if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Command $($Args -join ' ')" }
}

function Set-CommonEnv {
  $env:BOT_MODE = "paper"
  $env:BOT_HYBRID_LIVE = "0"
  $env:BOT_HYBRID_VALIDATION_ONLY = "1"
  $env:BOT_STRATEGY_MODE = "profit_dca"
  $env:BOT_COMPOUND_PROFITS = "1"
  $env:BOT_HALT_ON_REALIZED_LOSS = "1"
  $env:BOT_CROSS = "1"
  $env:BOT_CROSS_LIVE = "0"
  $env:BOT_PREFLIGHT_SYMBOL = $Symbol
  $env:BOT_PREFLIGHT_NOTIONAL_USD = "$Notional"
  $env:BOT_ADAPTER = $Adapter
}

if (-not $SkipInstall) {
  Step "DEPENDENCIES"
  if (Test-Path "requirements.txt") { Run "python" @("-m","pip","install","-r","requirements.txt") }
}

if ($Stage -in @("audit","all")) {
  Step "AUDIT"
  Run "python" @("-m","compileall","-q","arb_bot")
  Run "git" @("status","--short","--branch")
}

if ($Stage -in @("architecture","all")) {
  Step "ARCHITECTURE"
  Set-CommonEnv
  Run "python" @("-m","pytest","-q","tests/test_hybrid_engine.py")
  Run "python" @("-c","from arbx.hybrid.requirements import validate_capabilities; from arbx.hybrid.router import validate_route; print('native VenueAdapter contract: import OK; route gate: import OK')")
}

if ($Stage -in @("test","all")) {
  Step "FULL TEST SUITE"
  Run "python" @("-m","pytest","-q")
}

if ($Stage -in @("paper","all")) {
  Step "PAPER"
  Set-CommonEnv
  $env:BOT_MODE = "paper"
  $env:BOT_HYBRID_VALIDATION_ONLY = "0"
  Run "python" @("-m","arbx.cli","hybrid-validate")
}

if ($Stage -in @("venue-verify","all")) {
  Step "VENUE CERTIFICATION"
  foreach ($venue in $Venues) {
    $env:BOT_MODE = "paper"
    $env:BOT_HYBRID_LIVE = "0"
    $env:BOT_HYBRID_VALIDATION_ONLY = "1"
    $env:BOT_ADAPTER = $Adapter
    $envName = "BOT_" + $venue.ToUpperInvariant() + "_ADAPTER"
    Set-Item -Path ("Env:" + $envName) -Value $Adapter
    Write-Host ""
    Write-Host ("--- " + $venue + " / " + $Adapter + " / " + $Symbol + " / " + $Notional + " USD ---") -ForegroundColor Yellow
    Run "python" @("-m","arbx.cli","hybrid-validate",$venue)
  }
}

if ($Stage -in @("live-preflight","live","all")) {
  Step "LIVE PREFLIGHT — NO ORDERS"
  Set-CommonEnv
  $env:BOT_MODE = "live"
  $env:BOT_HYBRID_LIVE = "0"
  $env:BOT_HYBRID_VALIDATION_ONLY = "0"
  $env:BOT_CROSS_LIVE = "1"

  foreach ($venue in $Venues) {
    $envName = "BOT_" + $venue.ToUpperInvariant() + "_ADAPTER"
    Set-Item -Path ("Env:" + $envName) -Value $Adapter
    Write-Host ""
    Write-Host ("--- LIVE PREFLIGHT: " + $venue + " ---") -ForegroundColor Yellow
    Run "python" @("-m","arbx.cli","hybrid-validate",$venue)
  }

  Write-Host ""
  Write-Host "Preflight complete. This stage performs certification only; it does not place orders or move funds." -ForegroundColor Green
  Write-Host "Native adapter mode is fail-closed until a concrete native transport is implemented and independently certified." -ForegroundColor Yellow
}

if ($Stage -eq "live") {
  Step "LIVE ENGINE — EXPLICIT ARMING REQUIRED"
  Write-Host "This stage can submit real orders. Run only after every requested venue is independently LIVE_ELIGIBLE." -ForegroundColor Red
  $arm = Read-Host "Type ARM-NU-ARB-LIVE to continue"
  if ($arm -ne "ARM-NU-ARB-LIVE") { throw "Live engine not armed." }

  Set-CommonEnv
  $env:BOT_MODE = "live"
  $env:BOT_HYBRID_LIVE = "1"
  $env:BOT_HYBRID_VALIDATION_ONLY = "0"
  $env:BOT_CROSS = "1"
  $env:BOT_CROSS_LIVE = "1"
  $env:BOT_STRATEGY_MODE = "profit_dca"
  $env:BOT_COMPOUND_PROFITS = "1"
  $env:BOT_HALT_ON_REALIZED_LOSS = "1"

  foreach ($venue in $Venues) {
    $envName = "BOT_" + $venue.ToUpperInvariant() + "_ADAPTER"
    Set-Item -Path ("Env:" + $envName) -Value $Adapter
  }

  Write-Host "LIVE ARMING: $Notional USD starter allocation. Profit compounds; realized loss halts new engagements." -ForegroundColor Red
  Write-Host "IMPORTANT: this is real trading and is not a no-loss guarantee." -ForegroundColor Red
  Run "python" @("-m","arbx.cli","run")
}

Write-Host ""
Write-Host "NU-ARB upgrade workflow complete." -ForegroundColor Green
