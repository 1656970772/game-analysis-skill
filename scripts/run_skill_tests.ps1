param(
    [string]$SkillRoot = "",
    [string[]]$PytestArgs = @("tests"),
    [switch]$InstallMissing
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8

if ([string]::IsNullOrWhiteSpace($SkillRoot)) {
    $SkillRoot = Split-Path -Parent $PSScriptRoot
}

if (-not (Test-Path -LiteralPath $SkillRoot -PathType Container)) {
    throw "SkillRoot does not exist: $SkillRoot"
}

$skillRootPath = (Resolve-Path -LiteralPath $SkillRoot).Path
$venvPython = Join-Path -Path $skillRootPath -ChildPath "tools\python-env\Scripts\python.exe"
$sitePackages = Join-Path -Path $skillRootPath -ChildPath "tools\python-env\Lib\site-packages"

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Skill Python environment is missing. Run scripts\setup_python_env.ps1 first: $venvPython"
}

if (-not (Test-Path -LiteralPath $sitePackages -PathType Container)) {
    throw "Skill Python site-packages directory is missing: $sitePackages"
}

function Test-PythonModule {
    param(
        [Parameter(Mandatory = $true)][string]$ModuleName
    )

    & $venvPython -c "import $ModuleName" | Out-Null
    return $LASTEXITCODE -eq 0
}

if (-not (Test-PythonModule -ModuleName "UnityPy")) {
    throw "UnityPy is missing from the Skill Python environment. Run scripts\setup_python_env.ps1."
}

if (-not (Test-PythonModule -ModuleName "pytest")) {
    if ($InstallMissing) {
        & $venvPython -m pip install pytest
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install pytest into Skill Python environment."
        }
    } else {
        throw "pytest is missing from Skill Python environment. Re-run with -InstallMissing or install pytest into tools\python-env."
    }
}

$previousPythonPath = $env:PYTHONPATH
try {
    if ([string]::IsNullOrWhiteSpace($previousPythonPath)) {
        $env:PYTHONPATH = $sitePackages
    } else {
        $env:PYTHONPATH = "$sitePackages;$previousPythonPath"
    }

    Push-Location -LiteralPath $skillRootPath
    try {
        & $venvPython -m pytest @PytestArgs
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
