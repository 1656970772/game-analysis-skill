param(
    [string]$SkillRoot = "",
    [string]$ToolVersion = "1.0.0",
    [string]$BuildRoot = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8

function Get-NormalizedChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $trimChars = [char[]]@('\', '/')
    $rootPrefix = $resolvedRoot.TrimEnd($trimChars) + [System.IO.Path]::DirectorySeparatorChar

    if (-not $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate on '$fullPath': target is outside allowed root '$resolvedRoot'."
    }

    return $fullPath
}

function Remove-DirectorySafely {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$AllowedRoot
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $safePath = Get-NormalizedChildPath -Path (Resolve-Path -LiteralPath $Path).Path -Root $AllowedRoot
    Remove-Item -LiteralPath $safePath -Recurse -Force
}

function Invoke-NpmCommand {
    param(
        [Parameter(Mandatory = $true)][string]$NpmPath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $NpmPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "npm $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Assert-FileExists {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Expected file does not exist: $Path"
    }
}

if ([string]::IsNullOrWhiteSpace($SkillRoot)) {
    $SkillRoot = Split-Path -Parent $PSScriptRoot
}

if ([string]::IsNullOrWhiteSpace($ToolVersion)) {
    throw "ToolVersion cannot be empty."
}

if ($ToolVersion.IndexOfAny([System.IO.Path]::GetInvalidFileNameChars()) -ge 0) {
    throw "ToolVersion contains characters that cannot be used in a directory name: $ToolVersion"
}

if (-not (Test-Path -LiteralPath $SkillRoot -PathType Container)) {
    throw "SkillRoot does not exist: $SkillRoot"
}

$skillRootPath = (Resolve-Path -LiteralPath $SkillRoot).Path
$sourceDir = Join-Path -Path $skillRootPath -ChildPath "assets\model-gallery-viewer-src"
if ([string]::IsNullOrWhiteSpace($BuildRoot)) {
    $BuildRoot = Join-Path -Path $skillRootPath -ChildPath "tools\_build\GameModelViewer_$ToolVersion"
}
$buildRoot = Get-NormalizedChildPath -Path $BuildRoot -Root $skillRootPath
$builtInToolsRoot = Join-Path -Path $skillRootPath -ChildPath "built-in-tools"
$targetDirName = "GameModelViewer_$ToolVersion"

if ($ToolVersion -eq "1.0.0") {
    $targetDirName = "GameModelViewer_1.0.0"
}

$workDir = Join-Path -Path $buildRoot -ChildPath "GameModelViewer"
$targetDir = Join-Path -Path $builtInToolsRoot -ChildPath $targetDirName
$stagingTargetDir = Join-Path -Path $builtInToolsRoot -ChildPath "$targetDirName.staging"

if (-not (Test-Path -LiteralPath $sourceDir -PathType Container)) {
    throw "Model viewer source directory was not found: $sourceDir"
}

$lockFile = Join-Path -Path $sourceDir -ChildPath "package-lock.json"
if (-not (Test-Path -LiteralPath $lockFile -PathType Leaf)) {
    throw "package-lock.json was not found. Regenerate it before building: $lockFile"
}

$npmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
if (-not $npmCommand) {
    $npmCommand = Get-Command "npm" -ErrorAction SilentlyContinue
}
if (-not $npmCommand) {
    throw "npm was not found. Install Node.js/npm and make sure PATH is available."
}
$npmPath = $npmCommand.Source

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $builtInToolsRoot -Force | Out-Null
$buildRoot = (Resolve-Path -LiteralPath $buildRoot).Path
$builtInToolsRoot = (Resolve-Path -LiteralPath $builtInToolsRoot).Path
$workDir = Get-NormalizedChildPath -Path $workDir -Root $buildRoot
$targetDir = Get-NormalizedChildPath -Path $targetDir -Root $builtInToolsRoot
$stagingTargetDir = Get-NormalizedChildPath -Path $stagingTargetDir -Root $builtInToolsRoot

Remove-DirectorySafely -Path $workDir -AllowedRoot $buildRoot
Remove-DirectorySafely -Path $stagingTargetDir -AllowedRoot $builtInToolsRoot

New-Item -ItemType Directory -Path $workDir -Force | Out-Null

Get-ChildItem -LiteralPath $sourceDir -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $workDir -Recurse -Force
}

Push-Location -LiteralPath $workDir
try {
    $installCommandDisplay = "npm ci"
    Invoke-NpmCommand -NpmPath $npmPath -Arguments @("ci")
    Invoke-NpmCommand -NpmPath $npmPath -Arguments @("run", "dist")

    $builtExe = Join-Path -Path $workDir -ChildPath "dist\GameModelViewer.exe"
    Assert-FileExists -Path $builtExe

    New-Item -ItemType Directory -Path $stagingTargetDir -Force | Out-Null

    $stagedExe = Join-Path -Path $stagingTargetDir -ChildPath "GameModelViewer.exe"
    Copy-Item -LiteralPath $builtExe -Destination $stagedExe -Force

    $stagedVersionPath = Join-Path -Path $stagingTargetDir -ChildPath "VERSION.txt"
    $versionText = @(
        "Name: GameModelViewer",
        "Version: $ToolVersion",
        "BuiltAt: $((Get-Date).ToString('o'))",
        "SourceDir: $sourceDir",
        "WorkDir: $workDir",
        "InstallCommand: $installCommandDisplay"
    )
    Set-Content -LiteralPath $stagedVersionPath -Value $versionText -Encoding UTF8

    $stagedLicenseDir = Join-Path -Path $stagingTargetDir -ChildPath "LICENSES"
    New-Item -ItemType Directory -Path $stagedLicenseDir -Force | Out-Null
    $stagedLicensePath = Join-Path -Path $stagedLicenseDir -ChildPath "THIRD_PARTY_LICENSES.json"
    $licenseCommandDisplay = "npm ls --json --long"
    $licenseJson = & $npmPath ls --json --long
    $licenseExitCode = $LASTEXITCODE
    if (-not $licenseJson) {
        throw "$licenseCommandDisplay did not output a dependency license snapshot."
    }
    Set-Content -LiteralPath $stagedLicensePath -Value $licenseJson -Encoding UTF8
    if ($licenseExitCode -ne 0) {
        Write-Warning "$licenseCommandDisplay returned exit code $licenseExitCode; output was saved to $stagedLicensePath."
    }

    Assert-FileExists -Path $stagedExe
    Assert-FileExists -Path $stagedVersionPath
    Assert-FileExists -Path $stagedLicensePath

    Remove-DirectorySafely -Path $targetDir -AllowedRoot $builtInToolsRoot
    Move-Item -LiteralPath $stagingTargetDir -Destination $targetDir

    $targetExe = Join-Path -Path $targetDir -ChildPath "GameModelViewer.exe"
    Assert-FileExists -Path $targetExe
}
finally {
    Pop-Location
}

Write-Output (Join-Path -Path $targetDir -ChildPath "GameModelViewer.exe")
