[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$SandboxEvidencePath
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($RepositoryRoot)
if ([string]::IsNullOrWhiteSpace($SandboxEvidencePath)) {
    $SandboxEvidencePath = Join-Path $root "diagnostics/sandbox-readiness.json"
}

function Get-RepoText([string]$RelativePath) {
    $path = Join-Path $root $RelativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    return [System.IO.File]::ReadAllText($path)
}

function Has-Pattern([string]$Text, [string]$Pattern) {
    return ($null -ne $Text) -and [regex]::IsMatch(
        $Text, $Pattern, [System.Text.RegularExpressions.RegexOptions]::Multiline
    )
}

function Add-MatrixRow(
    [System.Collections.Generic.List[object]]$Rows,
    [string]$Check, [string]$Status, [string]$Evidence, [string]$Limitation
) {
    $Rows.Add([pscustomobject]@{
        check = $Check
        status = $Status
        evidence = $Evidence
        limitation = $Limitation
    })
}

function Get-CurrentCommit {
    try {
        $commit = & git -C $root rev-parse HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $commit -match '^[0-9a-fA-F]{40}$') {
            return $commit.ToLowerInvariant()
        }
    } catch { }
    return $null
}

function Get-SourceDigest([string[]]$RelativePaths) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.MemoryStream]::new()
    try {
        foreach ($relativePath in $RelativePaths) {
            $fullPath = Join-Path $root $relativePath
            if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { return $null }
            $pathBytes = [System.Text.Encoding]::UTF8.GetBytes($relativePath + "`n")
            $fileBytes = [System.IO.File]::ReadAllBytes($fullPath)
            $stream.Write($pathBytes, 0, $pathBytes.Length)
            $stream.Write($fileBytes, 0, $fileBytes.Length)
            $stream.WriteByte(10)
        }
        $stream.Position = 0
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Get-FileDigest([string]$Path) {
    $fileStream = [System.IO.File]::OpenRead($Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($fileStream))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
        $fileStream.Dispose()
    }
}

# Inspect only the named non-secret safety switches. No general environment
# enumeration is performed and no credential variable is read or emitted.
$controlExample = Get-RepoText ".env.control.example"
$configSource = Get-RepoText "arb_bot/arbx/config.py"
$gateSource = Get-RepoText "arb_bot/arbx/execution_gate.py"
$executorSource = Get-RepoText "arb_bot/arbx/execute.py"
$adapterSource = Get-RepoText "arb_bot/arbx/hybrid/adapters.py"
$workerSource = Get-RepoText "arb_bot/arbx/worker.py"
$hubSource = Get-RepoText "arb_bot/arbx/hub.py"
$requirementsSource = Get-RepoText "arb_bot/arbx/hybrid/requirements.py"
$registrySource = Get-RepoText "arb_bot/arbx/hybrid/registry.py"
$hybridEngineSource = Get-RepoText "arb_bot/arbx/hybrid/engine.py"
$scannerSource = Get-RepoText "arb_bot/arbx/hybrid/opportunity_finder.py"
$strategySource = Get-RepoText "arb_bot/arbx/strategy.py"
$riskSource = Get-RepoText "arb_bot/arbx/gate.py"
$journalSource = Get-RepoText "arb_bot/arbx/journal.py"
$launcherSource = Get-RepoText "arb_bot/start-live.ps1"
$documentation = Get-RepoText "docs/HYBRID_ENGINE.md"

$examplePaper = Has-Pattern $controlExample '(?m)^BOT_MODE=paper\s*$'
$exampleOrdersOff = Has-Pattern $controlExample '(?m)^BOT_ALLOW_ORDERS=0\s*$'
$codePaperDefault = Has-Pattern $configSource 'mode\s*=\s*os\.getenv\("BOT_MODE",\s*"paper"\)'
$codeOrdersOffDefault = Has-Pattern $gateSource 'os\.getenv\("BOT_ALLOW_ORDERS",\s*"0"\)'
$processMode = [System.Environment]::GetEnvironmentVariable("BOT_MODE", "Process")
$processOrderSwitch = [System.Environment]::GetEnvironmentVariable("BOT_ALLOW_ORDERS", "Process")
$effectiveMode = if ($null -eq $processMode) { "paper (code default)" } `
    elseif ($processMode -ceq "paper") { "paper" } `
    elseif ($processMode -ceq "live") { "live (unsafe)" } else { "non-paper value (unsafe)" }
$effectiveOrders = if ($null -eq $processOrderSwitch) { "0 (gate default)" } `
    elseif ($processOrderSwitch -ceq "0") { "0" } `
    elseif ($processOrderSwitch -ceq "1") { "1 (unsafe)" } else { "invalid value (unsafe)" }
$safeEffectiveSwitches = (($null -eq $processMode) -or ($processMode -ceq "paper")) `
    -and (($null -eq $processOrderSwitch) -or ($processOrderSwitch -ceq "0"))
$safeDefaults = $examplePaper -and $exampleOrdersOff -and $codePaperDefault `
    -and $codeOrdersOffDefault -and $safeEffectiveSwitches

$evidenceNames = @(
    "venue_live_eligible", "execution_eligible", "risk_approved",
    "capital_available", "route_eligible"
)
$gateHasAllEvidence = $true
foreach ($name in $evidenceNames) {
    if (-not (Has-Pattern $gateSource ([regex]::Escape($name)))) { $gateHasAllEvidence = $false }
}
$executionGateReady = (Has-Pattern $gateSource 'BOT_MODE') `
    -and (Has-Pattern $gateSource 'BOT_ALLOW_ORDERS') `
    -and $gateHasAllEvidence `
    -and (Has-Pattern $executorSource 'authorize_order') `
    -and (Has-Pattern $executorSource 'authorize_recovery') `
    -and (Has-Pattern $adapterSource 'no central execution gate') `
    -and (Has-Pattern $adapterSource 'execution_gate\.authorize\("hybrid"\)')

$orderCallPattern = '(?m)^\s*(?:\w+\s*=\s*)?(?:return\s+)?await\s+[\w.]+\.create_order\s*\('
$executeCalls = [System.Collections.Generic.List[object]]::new()
$adapterCalls = [System.Collections.Generic.List[object]]::new()
$unreviewedCalls = [System.Collections.Generic.List[string]]::new()
$applicationRoot = Join-Path $root "arb_bot"
if (Test-Path -LiteralPath $applicationRoot -PathType Container) {
    foreach ($pythonFile in (Get-ChildItem -LiteralPath $applicationRoot -Recurse -File -Filter "*.py")) {
        $source = [System.IO.File]::ReadAllText($pythonFile.FullName)
        $callMatches = [regex]::Matches($source, $orderCallPattern)
        if ($callMatches.Count -eq 0) { continue }
        $relativePath = $pythonFile.FullName.Substring($root.TrimEnd('\', '/').Length).
            TrimStart('\', '/').Replace('\', '/')
        foreach ($callMatch in $callMatches) {
            if ($relativePath -eq "arb_bot/arbx/execute.py") {
                $executeCalls.Add($callMatch)
            } elseif ($relativePath -eq "arb_bot/arbx/hybrid/adapters.py") {
                $adapterCalls.Add($callMatch)
            } else {
                $unreviewedCalls.Add($relativePath)
            }
        }
    }
}
$orderBoundariesReady = ($executeCalls.Count -eq 3) `
    -and ($adapterCalls.Count -eq 1) `
    -and ($unreviewedCalls.Count -eq 0) `
    -and (Has-Pattern $executorSource 'authorize_order\(permit\)') `
    -and (([regex]::Matches($executorSource, 'authorize_recovery\(permit\)')).Count -ge 2) `
    -and (Has-Pattern $adapterSource 'if self\.execution_gate is None') `
    -and (Has-Pattern $adapterSource 'execution_gate\.authorize\("hybrid"\)')

$venueSourceReady = (Has-Pattern $registrySource 'VENUE_CATALOG') `
    -and (Has-Pattern $hybridEngineSource 'validate_venue') `
    -and (Has-Pattern $hybridEngineSource 'live_eligible')
$authSourceReady = (Has-Pattern $gateSource 'BOT_MODE') `
    -and (Has-Pattern $gateSource 'BOT_ALLOW_ORDERS') `
    -and $gateHasAllEvidence
$marketDataSourceReady = ($null -ne $scannerSource) -and ($null -ne $strategySource)
$capitalAndRiskSourceReady = (Has-Pattern $configSource 'LIVE_STARTER_CAPITAL_USD\s*=\s*3\.0') `
    -and (Has-Pattern $riskSource 'class RiskManager') `
    -and (Has-Pattern $workerSource 'risk_approved') `
    -and (Has-Pattern $hubSource 'risk_approved') `
    -and (Has-Pattern $workerSource 'capital_available') `
    -and (Has-Pattern $hubSource 'capital_available') `
    -and (Has-Pattern $requirementsSource 'require_market_order')

# Reproducible sandbox evidence is a separate CI/operator artifact, never
# generated by this read-only script. Bind it to both HEAD and exact source bytes.
$evidenceFiles = @(
    ".env.control.example",
    "arb_bot/arbx/config.py",
    "arb_bot/arbx/execution_gate.py",
    "arb_bot/arbx/execute.py",
    "arb_bot/arbx/gate.py",
    "arb_bot/arbx/worker.py",
    "arb_bot/arbx/hub.py",
    "arb_bot/arbx/hybrid/requirements.py",
    "tests/test_execution_gate.py",
    "tests/test_live_limits.py",
    "tests/test_hybrid_engine.py",
    "arb_bot/arbx/selftest.py"
)
$currentCommit = Get-CurrentCommit
$currentSourceDigest = Get-SourceDigest $evidenceFiles
$sandboxEvidenceStatus = "UNVERIFIED"
$sandboxEvidenceSummary = "Required evidence file is absent; static source inspection cannot establish a sandbox run."
$sandboxEvidenceValid = $false
$requiredChecks = @{
    paper_selftest = @{
        command = "python run.py selftest"
        workingDirectory = "arb_bot"
        successPattern = "SELFTEST PASSED"
    }
    execution_gate_tests = @{
        command = "python tests/test_execution_gate.py"
        workingDirectory = "."
        successPattern = "(?s)Ran \d+ tests.*\r?\nOK"
    }
    live_limits_tests = @{
        command = "python -m pytest -q tests/test_live_limits.py"
        workingDirectory = "."
        successPattern = "\d+ passed"
    }
    hybrid_engine_tests = @{
        command = "python -m pytest -q tests/test_hybrid_engine.py"
        workingDirectory = "."
        successPattern = "\d+ passed"
    }
}
if (Test-Path -LiteralPath $SandboxEvidencePath -PathType Leaf) {
    try {
        $artifact = Get-Content -LiteralPath $SandboxEvidencePath -Raw | ConvertFrom-Json
        $checksValid = $true
        $evidenceDirectory = [System.IO.Path]::GetFullPath((Split-Path -Parent $SandboxEvidencePath))
        foreach ($checkName in $requiredChecks.Keys) {
            $expectation = $requiredChecks[$checkName]
            $check = $artifact.checks.$checkName
            if ($null -eq $check -or $check.status -cne "PASSED" -or $check.exitCode -ne 0 `
                -or $check.command -cne $expectation.command `
                -or $check.workingDirectory -cne $expectation.workingDirectory `
                -or [string]::IsNullOrWhiteSpace([string]$check.logPath) `
                -or [string]$check.logSha256 -notmatch '^[0-9a-fA-F]{64}$') {
                $checksValid = $false
                continue
            }
            $logPath = [System.IO.Path]::GetFullPath((Join-Path $evidenceDirectory ([string]$check.logPath)))
            if (-not $logPath.StartsWith($evidenceDirectory + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) `
                -or -not (Test-Path -LiteralPath $logPath -PathType Leaf)) {
                $checksValid = $false
                continue
            }
            if ((Get-FileDigest $logPath) -cne ([string]$check.logSha256).ToLowerInvariant()) {
                $checksValid = $false
                continue
            }
            $logText = [System.IO.File]::ReadAllText($logPath)
            if (-not [regex]::IsMatch($logText, $expectation.successPattern)) {
                $checksValid = $false
            }
        }
        $timestamp = [DateTimeOffset]::MinValue
        $timestampValid = [DateTimeOffset]::TryParse(
            [string]$artifact.completedUtc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal,
            [ref]$timestamp
        )
        $fresh = $timestampValid -and $timestamp -le [DateTimeOffset]::UtcNow `
            -and $timestamp -ge [DateTimeOffset]::UtcNow.AddDays(-7)
        $sandboxEvidenceValid = ($artifact.schemaVersion -eq 1) `
            -and ($artifact.status -ceq "PASSED") `
            -and ($null -ne $currentCommit) `
            -and ([string]$artifact.commit -ceq $currentCommit) `
            -and ($null -ne $currentSourceDigest) `
            -and ([string]$artifact.sourceSha256 -ceq $currentSourceDigest) `
            -and $checksValid -and $fresh
        if ($sandboxEvidenceValid) {
            $sandboxEvidenceStatus = "PASS"
            $sandboxEvidenceSummary = "Fresh sandbox evidence matches current commit/source digest and all four required check results."
        } else {
            $sandboxEvidenceStatus = "BLOCKED"
            $sandboxEvidenceSummary = "Evidence is malformed, stale, failed, or does not match current commit/source bytes."
        }
    } catch {
        $sandboxEvidenceStatus = "BLOCKED"
        $sandboxEvidenceSummary = "Evidence could not be parsed and validated."
    }
}

$matrices = [System.Collections.Generic.List[object]]::new()

$rows = [System.Collections.Generic.List[object]]::new()
Add-MatrixRow $rows "Venue registry and certification implementation" $(if ($venueSourceReady) { "PASS" } else { "BLOCKED" }) `
    "Static source inspection of registry and validator" "Catalog presence is not runtime certification."
Add-MatrixRow $rows "Current venue eligibility" "UNVERIFIED" `
    "No fresh authenticated venue certification is consumed" "No venue is declared certified."
$matrices.Add([pscustomobject]@{ name = "VENUE"; rows = $rows.ToArray() })

$rows = [System.Collections.Generic.List[object]]::new()
Add-MatrixRow $rows "Credential handling or authenticated account evidence" "UNVERIFIED" `
    "Credentials are intentionally not read; runtime account evidence is not supplied" "No authentication is claimed."
Add-MatrixRow $rows "Required live authorization gates exist" $(if ($authSourceReady) { "PASS" } else { "BLOCKED" }) `
    "Source requires BOT_MODE, BOT_ALLOW_ORDERS, and all five exact-true evidence flags" "Static code evidence is not an authenticated session."
$matrices.Add([pscustomobject]@{ name = "AUTH"; rows = $rows.ToArray() })

$rows = [System.Collections.Generic.List[object]]::new()
Add-MatrixRow $rows "Scanner and market-data implementation exists" $(if ($marketDataSourceReady) { "PASS" } else { "BLOCKED" }) `
    "Static scanner and strategy source inspection" "Runtime book freshness/depth and public/private streams are not verified."
Add-MatrixRow $rows "Fresh stream/scanner evidence" "UNVERIFIED" `
    "No runtime stream or scan evidence is consumed" "No network request is made."
$matrices.Add([pscustomobject]@{ name = "MARKET DATA"; rows = $rows.ToArray() })

$rows = [System.Collections.Generic.List[object]]::new()
Add-MatrixRow $rows "Read-only side-effect policy" "PASS" `
    "Script performs local source/artifact reads only; it contains no exchange client invocation" "No connectivity or network health is tested."
Add-MatrixRow $rows "Exchange/network reachability" "UNVERIFIED" `
    "Network access is deliberately disabled for this audit" "Connectivity must be established by separate authorized verification."
$matrices.Add([pscustomobject]@{ name = "NETWORK"; rows = $rows.ToArray() })

$rows = [System.Collections.Generic.List[object]]::new()
Add-MatrixRow $rows "Central switches and evidence gate" $(if ($executionGateReady) { "PASS" } else { "BLOCKED" }) `
    "ExecutionGate source checks both required switches and five positive evidence gates" "This does not authorize an order."
Add-MatrixRow $rows "All production submission boundaries" $(if ($orderBoundariesReady) { "PASS" } else { "BLOCKED" }) `
    "Observed execute=$($executeCalls.Count), hybrid-adapter=$($adapterCalls.Count), unreviewed=$($unreviewedCalls.Count)" `
    "No order is submitted; hybrid adapter remains unavailable without positive evidence."
$matrices.Add([pscustomobject]@{ name = "EXECUTION"; rows = $rows.ToArray() })

$rows = [System.Collections.Generic.List[object]]::new()
Add-MatrixRow $rows "Risk, starter capital, and route source gates" $(if ($capitalAndRiskSourceReady) { "PASS" } else { "BLOCKED" }) `
    'Static source inspection of RiskManager, $3 live sizing, and route/capital checks' "Current balance and risk decision are not proven."
Add-MatrixRow $rows "Current risk and capital approval" "UNVERIFIED" `
    "No balance or runtime risk decision is consumed" "Missing runtime values deny live eligibility."
$matrices.Add([pscustomobject]@{ name = "RISK"; rows = $rows.ToArray() })

$rows = [System.Collections.Generic.List[object]]::new()
Add-MatrixRow $rows "Paper mode and explicit orders-off defaults" $(if ($safeDefaults) { "PASS" } else { "BLOCKED" }) `
    ".env.control.example and code defaults; effective BOT_MODE=$effectiveMode; BOT_ALLOW_ORDERS=$effectiveOrders" `
    "Only these two non-secret environment values are inspected; unsafe or missing explicit values fail closed."
Add-MatrixRow $rows "Reproducible sandbox run evidence" $sandboxEvidenceStatus `
    $sandboxEvidenceSummary "Requires a fresh artifact tied to current HEAD/source and passing paper, gate, live-limit, and hybrid tests."
$matrices.Add([pscustomobject]@{ name = "SANDBOX"; rows = $rows.ToArray() })

$allStaticChecks = $safeDefaults -and $venueSourceReady -and $authSourceReady `
    -and $marketDataSourceReady -and $executionGateReady -and $orderBoundariesReady `
    -and $capitalAndRiskSourceReady
$finalState = if ($allStaticChecks -and $sandboxEvidenceValid) { "SANDBOX_READY" } else { "NOT_READY" }

$report = [pscustomobject]@{
    schemaVersion = 1
    state = $finalState
    readinessBasis = "Repository checks plus reproducible sandbox artifact; no live/runtime certification."
    evidenceBinding = [pscustomobject]@{
        commit = $currentCommit
        sourceSha256 = $currentSourceDigest
        sourceFiles = $evidenceFiles
        sandboxEvidencePath = [System.IO.Path]::GetFullPath($SandboxEvidencePath)
    }
    sideEffects = [pscustomobject]@{
        networkRequests = $false
        ordersSubmitted = $false
        environmentChanged = $false
        applicationCodeExecuted = $false
        journalOrDatabaseOpened = $false
    }
    matrices = $matrices.ToArray()
    liveReadinessCeiling = "LIVE_SCAN_READY and LIVE_EXECUTION_READY require separate fresh authenticated evidence and are never inferred here."
}

$report | ConvertTo-Json -Depth 8
if ($finalState -eq "NOT_READY") { exit 1 }
exit 0
