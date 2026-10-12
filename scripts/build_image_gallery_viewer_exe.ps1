param(
    [string]$SkillRoot = "",
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
chcp 65001 > $null

function Resolve-GameAnalysisSkillRoot {
    param(
        [string]$ProvidedSkillRoot
    )

    if (-not [string]::IsNullOrWhiteSpace($ProvidedSkillRoot)) {
        $resolved = Resolve-Path -LiteralPath $ProvidedSkillRoot -ErrorAction Stop
        return $resolved.ProviderPath
    }

    if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        throw "Cannot infer GameAnalysis skill root because PSScriptRoot is empty. Pass -SkillRoot explicitly."
    }

    $scriptDir = (Resolve-Path -LiteralPath $PSScriptRoot -ErrorAction Stop).ProviderPath
    $candidate = Split-Path -LiteralPath $scriptDir -Parent
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        throw "Cannot infer GameAnalysis skill root from script directory '$scriptDir'. Pass -SkillRoot explicitly."
    }

    $templateCandidate = Join-Path $candidate "assets\image-gallery-viewer-src"
    if (-not (Test-Path -LiteralPath $templateCandidate -PathType Container)) {
        throw "Cannot infer a valid GameAnalysis skill root from '$scriptDir'. Expected template directory '$templateCandidate'. Pass -SkillRoot explicitly."
    }

    return $candidate
}

function Assert-PathInside {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$ParentPath
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    $fullParent = [IO.Path]::GetFullPath($ParentPath)
    $parentWithSeparator = $fullParent
    if (-not $parentWithSeparator.EndsWith([IO.Path]::DirectorySeparatorChar)) {
        $parentWithSeparator = $parentWithSeparator + [IO.Path]::DirectorySeparatorChar
    }

    if (
        -not $fullPath.Equals($fullParent, [StringComparison]::OrdinalIgnoreCase) -and
        -not $fullPath.StartsWith($parentWithSeparator, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Refusing to operate outside expected directory '$fullParent': '$fullPath'"
    }
}

$skillRootPath = Resolve-GameAnalysisSkillRoot -ProvidedSkillRoot $SkillRoot
$templateDir = Join-Path $skillRootPath "assets\image-gallery-viewer-src"
$targetRelative = "built-in-tools\GameImageGallery_$Version"
$builtInToolsRoot = Join-Path $skillRootPath "built-in-tools"
$targetDir = Join-Path $skillRootPath $targetRelative
$target = Join-Path $targetDir "GameImageGallery.exe"
$buildRoot = Join-Path $skillRootPath "tools\_build"
$workRelative = "tools\_build\GameImageGallery"
$workDir = Join-Path $skillRootPath $workRelative

if (-not (Test-Path -LiteralPath $templateDir -PathType Container)) {
    throw "Image gallery viewer template directory not found: $templateDir"
}

$script:npmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
if (-not $script:npmCommand) {
    $script:npmCommand = Get-Command "npm" -ErrorAction SilentlyContinue
}
if (-not $script:npmCommand) {
    throw "npm was not found. Install Node.js/npm or add npm to PATH before building GameImageGallery."
}

function Invoke-Npm {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$NpmArguments
    )

    & $script:npmCommand.Source @NpmArguments
    if ($LASTEXITCODE -ne 0) {
        throw "npm $($NpmArguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
if (Test-Path -LiteralPath $workDir) {
    Assert-PathInside -Path $workDir -ParentPath $buildRoot
    Remove-Item -LiteralPath $workDir -Recurse -Force
}
New-Item -ItemType Directory -Path $workDir -Force | Out-Null

Get-ChildItem -LiteralPath $templateDir | Copy-Item -Destination $workDir -Recurse -Force

Push-Location -LiteralPath $workDir
try {
    Invoke-Npm install
    Invoke-Npm run dist
}
finally {
    Pop-Location
}

$unpackedDir = Join-Path $workDir "dist\win-unpacked"
if (-not (Test-Path -LiteralPath $unpackedDir -PathType Container)) {
    throw "Electron build did not create expected unpacked Windows app directory: $unpackedDir"
}

New-Item -ItemType Directory -Path $builtInToolsRoot -Force | Out-Null
if (Test-Path -LiteralPath $targetDir) {
    Assert-PathInside -Path $targetDir -ParentPath $builtInToolsRoot
    Remove-Item -LiteralPath $targetDir -Recurse -Force
}
New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
Get-ChildItem -LiteralPath $unpackedDir -Force | Copy-Item -Destination $targetDir -Recurse -Force

if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw "Expected unpacked GameImageGallery entry EXE was not found after copying: $target"
}

Write-Output $target
