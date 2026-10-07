param(
    [string]$Symbol = "BTC/USDT"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$diagnostics = Join-Path $repoRoot "diagnostics"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project virtualenv Python is missing: $python. Create .venv and install arb_bot\requirements.txt first."
}
if ($Symbol -notmatch '^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$') {
    throw "Symbol must be a canonical BASE/QUOTE pair, such as BTC/USDT."
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "Administrator elevation is unnecessary for these checks. This script will only run local tests and public read-only market-data audits."
}

Set-Location -LiteralPath (Join-Path $repoRoot "arb_bot")
$env:BOT_MODE = "paper"
$env:BOT_ALLOW_ORDERS = "0"
$env:BOT_PREFLIGHT_SYMBOL = $Symbol

function Invoke-Check {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    & $python @Arguments | ForEach-Object { Write-Host $_ }
    $code = $LASTEXITCODE
    Write-Host "$Name exit code: $code"
    return $code
}

$pipExit = Invoke-Check "Dependency consistency" @("-m", "pip", "check")
if ($pipExit -ne 0) {
    throw "Dependency check failed. Resolve the reported project-venv dependencies before continuing."
}

$selfTestExit = Invoke-Check "Offline self-test" @("run.py", "selftest")
if ($selfTestExit -ne 0) {
    throw "Offline self-test failed; public audits were not started."
}

$randomAuditExit = Invoke-Check "Randomized all-venue REST+WebSocket+depth audit" @(
    "run.py", "randomized-venue-feed-audit"
)
$requiredAuditExit = Invoke-Check "Required venue public-feed audit" @(
    "run.py", "required-live-venue-audit"
)
$readinessExit = Invoke-Check "Persist readiness state" @(
    "run.py", "readiness-state"
)
if ($readinessExit -ne 0) {
    throw "Readiness report generation failed."
}

$latest = Join-Path $diagnostics "live-readiness-state-latest.json"
if (-not (Test-Path -LiteralPath $latest -PathType Leaf)) {
    throw "Persistent readiness report is missing: $latest"
}
$report = Get-Content -LiteralPath $latest -Raw | ConvertFrom-Json
if ($report.persistenceValidation.validated -ne $true) {
    throw "Persistent readiness report did not pass its read-back validation."
}

Write-Host "`n=== Live engagement summary ===" -ForegroundColor Cyan
Write-Host "Target mode: $($report.liveModeRequirement.targetMode)"
Write-Host "Readiness: $($report.liveModeRequirement.readinessState)"
Write-Host "Authorized: $($report.liveModeRequirement.authorized)"
Write-Host "Orders enabled: $($report.liveModeRequirement.ordersEnabled)"
Write-Host "Live candidate venues: $($report.liveModeRequirement.eligibleVenueCandidates -join ', ')"
Write-Host "Currently certified venues: $(if ($report.liveModeRequirement.currentlyVerifiedVenues.Count) { $report.liveModeRequirement.currentlyVerifiedVenues -join ', ' } else { 'none' })"
Write-Host "Blockers: $($report.liveModeRequirement.blockingReasons -join '; ')"
Write-Host "Persisted report: $latest"
Write-Host "Public audit exit codes: randomized=$randomAuditExit required=$requiredAuditExit"
Write-Host "No credentials, balances, private streams, orders, or transfers were accessed."

if ($report.liveModeRequirement.authorized -ne $true -or $report.liveModeRequirement.ordersEnabled -ne $true) {
    Write-Host "Live execution is not certified. Keep the engine in paper mode; this script does not enable live orders." -ForegroundColor Yellow
    exit 2
}
