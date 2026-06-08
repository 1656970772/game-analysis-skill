param(
  [Parameter(Mandatory = $true)]
  [string]$StudyRoot,

  [string]$SkillRoot = "",

  [string]$OutputExeName = "GameArtStudyGallery.exe"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($SkillRoot)) {
  if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    throw "SkillRoot was not provided and PSScriptRoot is empty. Pass -SkillRoot explicitly."
  }
  $SkillRoot = Split-Path -Parent $PSScriptRoot
}

$resolvedStudyRoot = (Resolve-Path -LiteralPath $StudyRoot).Path
$templateDir = Join-Path $SkillRoot "assets\electron-gallery-viewer"
$workDir = Join-Path $resolvedStudyRoot "4.临时目录\工具产物\gallery-exe"

$galleryIndexCandidates = @(
  (Join-Path $resolvedStudyRoot "2.报告\全量图片画廊.html"),
  (Join-Path $resolvedStudyRoot "2.报告\全量图片画廊\index.html"),
  (Join-Path $resolvedStudyRoot "gallery\index.html")
)
$reportRoot = Join-Path $resolvedStudyRoot "2.报告"
if (Test-Path -LiteralPath $reportRoot) {
  $galleryIndexCandidates += Get-ChildItem -LiteralPath $reportRoot -Directory |
    ForEach-Object { Join-Path $_.FullName "index.html" }
}

function Invoke-Npm {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$Arguments
  )

  $npmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
  if (-not $npmCommand) {
    $npmCommand = Get-Command "npm" -ErrorAction Stop
  }
  & $npmCommand.Source @Arguments
}

if (-not ($galleryIndexCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1)) {
  throw "Cannot find gallery index.html under '$resolvedStudyRoot'. Build the HTML gallery first."
}

if (-not (Test-Path -LiteralPath $templateDir)) {
  throw "Cannot find Electron template: $templateDir"
}

New-Item -ItemType Directory -Force -Path $workDir | Out-Null
Copy-Item -LiteralPath (Join-Path $templateDir "main.js") -Destination (Join-Path $workDir "main.js") -Force
Copy-Item -LiteralPath (Join-Path $templateDir "preload.js") -Destination (Join-Path $workDir "preload.js") -Force
Copy-Item -LiteralPath (Join-Path $templateDir "package.json") -Destination (Join-Path $workDir "package.json") -Force

Push-Location $workDir
try {
  if ($env:NODE_OPTIONS -notmatch "--use-system-ca") {
    $env:NODE_OPTIONS = (($env:NODE_OPTIONS, "--use-system-ca") -join " ").Trim()
  }
  Invoke-Npm @("install")
  Invoke-Npm @("run", "self-test")
  Invoke-Npm @("run", "dist")
}
finally {
  Pop-Location
}

$portable = Get-ChildItem -LiteralPath (Join-Path $workDir "dist") -Filter "*.exe" -File |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if (-not $portable) {
  throw "Electron build finished but no .exe was found under '$workDir\dist'."
}

$targetDir = Join-Path $resolvedStudyRoot "2.报告"
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
$target = Join-Path $targetDir $OutputExeName
Copy-Item -LiteralPath $portable.FullName -Destination $target -Force
Write-Output $target
