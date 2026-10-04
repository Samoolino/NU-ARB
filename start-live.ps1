$ErrorActionPreference = "Stop"

$launcher = Join-Path $PSScriptRoot "arb_bot\start-live.ps1"
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Live launcher not found at '$launcher'."
}

& $launcher
