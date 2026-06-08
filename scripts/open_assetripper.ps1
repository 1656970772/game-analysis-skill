param(
  [string]$SkillRoot = (Split-Path -Parent (Split-Path -Parent $PSCommandPath)),
  [int]$Port = 0,
  [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$exe = Join-Path $SkillRoot "tools\AssetRipper_1.3.14\AssetRipper.GUI.Free.exe"
if (-not (Test-Path -LiteralPath $exe)) {
  throw "AssetRipper executable was not found: $exe"
}

$args = @()
if ($Port -gt 0) {
  $args += @("--port", [string]$Port)
}
if ($LogPath) {
  $args += @("--log", "true", "--log-path", $LogPath)
}

Start-Process -FilePath $exe -ArgumentList $args -WorkingDirectory (Split-Path -Parent $exe)
Write-Output "Started AssetRipper: $exe"
