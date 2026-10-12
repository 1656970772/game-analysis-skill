param(
    [string]$SkillRoot = "",
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
chcp 65001 > $null

function Resolve-SkillRoot {
    param([string]$Provided)
    if (-not [string]::IsNullOrWhiteSpace($Provided)) {
        return (Resolve-Path -LiteralPath $Provided -ErrorAction Stop).ProviderPath
    }
    return (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot) -ErrorAction Stop).ProviderPath
}

function Assert-Inside {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $fullPath = [IO.Path]::GetFullPath($Path)
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $prefix = $fullRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $fullPath.Equals($fullRoot, [StringComparison]::OrdinalIgnoreCase) -and -not $fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside '$fullRoot': '$fullPath'"
    }
    return $fullPath
}

function Remove-SafeDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    if (Test-Path -LiteralPath $Path) {
        $safe = Assert-Inside -Path (Resolve-Path -LiteralPath $Path).ProviderPath -Root $Root
        Remove-Item -LiteralPath $safe -Recurse -Force
    }
}

function Invoke-Npm {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $script:NpmPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "npm $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

$skillRootPath = Resolve-SkillRoot -Provided $SkillRoot
$sourceDir = Join-Path $skillRootPath "assets\vfx-viewer-src"
$buildRoot = Join-Path $skillRootPath "tools\_build"
$workDir = Join-Path $buildRoot "GameVfxViewer"
$builtInRoot = Join-Path $skillRootPath "built-in-tools"
$targetDir = Join-Path $builtInRoot "GameVfxViewer_$Version"
$targetExe = Join-Path $targetDir "GameVfxViewer.exe"

if (-not (Test-Path -LiteralPath $sourceDir -PathType Container)) {
    throw "VFX viewer source directory not found: $sourceDir"
}

$npmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
if (-not $npmCommand) {
    $npmCommand = Get-Command "npm" -ErrorAction SilentlyContinue
}
if (-not $npmCommand) {
    throw "npm was not found. Install Node.js/npm or add npm to PATH before building GameVfxViewer."
}
$script:NpmPath = $npmCommand.Source

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $builtInRoot -Force | Out-Null
Remove-SafeDirectory -Path $workDir -Root $buildRoot
New-Item -ItemType Directory -Path $workDir -Force | Out-Null

Get-ChildItem -LiteralPath $sourceDir -Force | Copy-Item -Destination $workDir -Recurse -Force

Push-Location -LiteralPath $workDir
try {
    Invoke-Npm install
    Invoke-Npm run dist
}
finally {
    Pop-Location
}

$builtExe = Join-Path $workDir "dist\GameVfxViewer.exe"
if (-not (Test-Path -LiteralPath $builtExe -PathType Leaf)) {
    throw "Electron build did not create expected GameVfxViewer.exe: $builtExe"
}

Remove-SafeDirectory -Path $targetDir -Root $builtInRoot
New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
Copy-Item -LiteralPath $builtExe -Destination $targetExe -Force

$versionPath = Join-Path $targetDir "VERSION.txt"
Set-Content -LiteralPath $versionPath -Encoding UTF8 -Value @(
    "Name: GameVfxViewer",
    "Version: $Version",
    "BuiltAt: $((Get-Date).ToString('o'))",
    "SourceDir: $sourceDir",
    "WorkDir: $workDir"
)

Write-Output $targetExe
