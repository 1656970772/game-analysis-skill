param(
  [string]$SkillRoot = (Split-Path -Parent (Split-Path -Parent $PSCommandPath)),
  [string]$VenvPath = ""
)

$ErrorActionPreference = "Stop"

# This script restores Python dependencies only. It does not download
# AssetRipper, Ghidra, UTMT, UModel, ILSpy, or other third-party tools.
if (-not $VenvPath) {
  $VenvPath = Join-Path $SkillRoot "tools\python-env"
}

$requirements = Join-Path $SkillRoot "requirements.txt"
if (-not (Test-Path -LiteralPath $requirements)) {
  throw "Cannot find requirements.txt: $requirements"
}

if (-not (Test-Path -LiteralPath $VenvPath)) {
  python -m venv $VenvPath
}

$python = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  throw "Cannot find venv python: $python"
}

& $python -m pip install --upgrade pip
& $python -m pip install -r $requirements
Write-Output $python
