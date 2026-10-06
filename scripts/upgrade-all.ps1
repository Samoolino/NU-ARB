[CmdletBinding()]
param(
  [ValidateSet("audit","ui","core","venue-verify","live-preflight","live","all")]
  [string]$Stage = "all",
  [string]$Symbol = "BTC/USDT",
  [decimal]$Notional = 3.00,
  [ValidateSet("ccxt_pro","ccxt","native")]
  [string]$Adapter = "ccxt_pro",
  [switch]$SkipInstall,
  [switch]$RunServerSmoke
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
function Step([string]$Name) { Write-Host ("=== " + $Name + " ===") -ForegroundColor Cyan }
function Require([string]$Path) { if (-not (Test-Path $Path)) { throw "Required path missing: $Path" } }
Step "NU-ARB upgrade audit"
git status --short
Require "index.html"
Require "arb_bot/arbx/config.py"
Require "arb_bot/arbx/hybrid/engine.py"
Require "scripts/upgrade-native-core.ps1"
if (-not $SkipInstall) {
  Step "Install frontend dependencies"
  npm install
  Step "Install Python validation dependencies"
  python -m pip install -r arb_bot/requirements.txt
  python -m pip install pytest ccxt ccxtpro
}
Step "Frontend static smoke"
$html = Get-Content "index.html" -Raw
$required = @("NU-ARB","EXECUTION COMMAND CENTER",'$3 starter',"compound realized profit","NO MARTINGALE","FAIL-CLOSED","engineVenue","selectedOptions","engineTradeSize")
foreach ($needle in $required) { if ($html -notmatch [regex]::Escape($needle)) { throw "Frontend smoke failed: missing '$needle'" } }
if ($html -match "max \$25|max \$3 pilot limit|18 configured public spot adapters") { throw "Frontend smoke failed: stale pilot UI copy remains" }
Write-Host "Frontend policy and control wiring checks passed." -ForegroundColor Green
if ($RunServerSmoke -and $Stage -in @("ui","all")) {
  Step "Static HTTP smoke"
  $proc = Start-Process -FilePath "python" -ArgumentList "-m http.server 4173 --bind 127.0.0.1" -WorkingDirectory $repoRoot -PassThru
  try {
    Start-Sleep -Seconds 2
    $response = Invoke-WebRequest "http://127.0.0.1:4173/index.html" -UseBasicParsing
    if ($response.StatusCode -ne 200 -or $response.Content.Length -lt 10000) { throw "Static HTTP smoke failed" }
    Write-Host "index.html served successfully." -ForegroundColor Green
  } finally { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
}
if ($Stage -in @("core","venue-verify","live-preflight","live","all")) {
  Step "Core compile and tests"
  $env:PYTHONPATH = "arb_bot"
  python -m compileall -q arb_bot
  python -m pytest -q tests/test_hybrid_engine.py
  npm test
}
if ($Stage -in @("venue-verify","live-preflight","live","all")) {
  Step "Credentialed venue certification"
  $env:BOT_HYBRID_VALIDATION_ONLY = "1"
  python -m arbx.cli hybrid-validate --symbol $Symbol
}
if ($Stage -in @("live-preflight","live","all")) {
  Step "Live preflight (NO ORDERS)"
  $env:BOT_MODE = "live"
  $env:BOT_HYBRID_LIVE = "1"
  $env:BOT_HYBRID_VALIDATION_ONLY = "1"
  $env:BOT_CROSS = "1"
  $env:BOT_CROSS_LIVE = "1"
  $env:BOT_STRATEGY_MODE = "profit_dca"
  $env:BOT_COMPOUND_PROFITS = "1"
  $env:BOT_HALT_ON_REALIZED_LOSS = "1"
  $env:BOT_STARTER_CAPITAL_USD = "3"
  python -m arbx.cli hybrid-validate --symbol $Symbol
}
if ($Stage -eq "live") {
  Step "LIVE ARM"
  Write-Host "This starts real CEX execution. CEX arbitrage is non-atomic; no-loss execution cannot be guaranteed." -ForegroundColor Yellow
  Write-Host "Policy: $3 starter, realized-profit compounding, realized-loss halt, no averaging down." -ForegroundColor Yellow
  $arm = Read-Host "Type ARM-NU-ARB-LIVE to continue"
  if ($arm -ne "ARM-NU-ARB-LIVE") { throw "Live arm cancelled." }
  $env:BOT_MODE = "live"
  $env:BOT_HYBRID_LIVE = "1"
  $env:BOT_HYBRID_VALIDATION_ONLY = "0"
  $env:BOT_CROSS = "1"
  $env:BOT_CROSS_LIVE = "1"
  $env:BOT_STRATEGY_MODE = "profit_dca"
  $env:BOT_COMPOUND_PROFITS = "1"
  $env:BOT_HALT_ON_REALIZED_LOSS = "1"
  $env:BOT_STARTER_CAPITAL_USD = "3"
  $env:BOT_TRADE_SIZE_USD = "3"
  python -m arbx.cli run
}
Step "Upgrade complete"
git status --short
