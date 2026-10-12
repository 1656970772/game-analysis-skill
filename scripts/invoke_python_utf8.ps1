<#
.SYNOPSIS
以 UTF-8 编码安全调用 GameAnalysis Skill 的 Python 脚本。

.DESCRIPTION
默认使用 Skill 自带 Python，也可通过 -PythonExe 指定其他 Python。
-Script 后未绑定的参数会按原参数边界直接传给 Python 脚本，不拼接命令字符串。

.PARAMETER Script
要执行的 Python 脚本路径。

.PARAMETER PythonExe
Python 可执行文件路径或可由 PATH 解析的命令名。

.PARAMETER ScriptArgs
传给 Python 脚本的其余参数，例如 --format json。
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Script,

    [string]$PythonExe,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs = @()
)

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $skillRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
    $PythonExe = Join-Path $skillRoot "tools\python-env\Scripts\python.exe"
}

function Resolve-PythonExecutable {
    param([string]$Candidate)

    if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Candidate).ProviderPath
    }

    if (-not [System.IO.Path]::IsPathRooted($Candidate) -and
        $Candidate.IndexOf([System.IO.Path]::DirectorySeparatorChar) -lt 0 -and
        $Candidate.IndexOf([System.IO.Path]::AltDirectorySeparatorChar) -lt 0) {
        $command = Get-Command -Name $Candidate -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $command) {
            return $command.Source
        }
    }

    return $null
}

$previousInputEncoding = [Console]::InputEncoding
$previousOutputEncoding = [Console]::OutputEncoding
$previousPipelineEncoding = $OutputEncoding
$previousPythonIoEncoding = [Environment]::GetEnvironmentVariable("PYTHONIOENCODING", "Process")
$previousPythonUtf8 = [Environment]::GetEnvironmentVariable("PYTHONUTF8", "Process")
$hadPythonIoEncoding = $null -ne $previousPythonIoEncoding
$hadPythonUtf8 = $null -ne $previousPythonUtf8
$exitCode = 1

try {
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [Console]::InputEncoding = $utf8WithoutBom
    [Console]::OutputEncoding = $utf8WithoutBom
    $OutputEncoding = $utf8WithoutBom
    [Environment]::SetEnvironmentVariable("PYTHONIOENCODING", "utf-8", "Process")
    [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Process")

    $resolvedPython = Resolve-PythonExecutable -Candidate $PythonExe
    if ($null -eq $resolvedPython) {
        [Console]::Error.WriteLine(
            "invoke_python_utf8.ps1: Python executable does not exist: {0}",
            $PythonExe
        )
        $exitCode = 127
    }
    elseif (-not (Test-Path -LiteralPath $Script -PathType Leaf)) {
        [Console]::Error.WriteLine(
            "invoke_python_utf8.ps1: Python script does not exist: {0}",
            $Script
        )
        $exitCode = 2
    }
    else {
        $resolvedScript = (Resolve-Path -LiteralPath $Script).ProviderPath
        # 使用调用运算符和参数数组，保留中文、空格及 --flag value 的参数边界.
        & $resolvedPython $resolvedScript @ScriptArgs
        $exitCode = $LASTEXITCODE
        if ($null -eq $exitCode) {
            $exitCode = 1
        }
    }
}
catch {
    [Console]::Error.WriteLine("invoke_python_utf8.ps1: failed to launch Python: {0}", $_.Exception.Message)
    $exitCode = 1
}
finally {
    # 恢复调用前状态；原本不存在的环境变量仍恢复为不存在.
    [Console]::InputEncoding = $previousInputEncoding
    [Console]::OutputEncoding = $previousOutputEncoding
    $OutputEncoding = $previousPipelineEncoding

    if ($hadPythonIoEncoding) {
        [Environment]::SetEnvironmentVariable("PYTHONIOENCODING", $previousPythonIoEncoding, "Process")
    }
    else {
        [Environment]::SetEnvironmentVariable("PYTHONIOENCODING", $null, "Process")
    }

    if ($hadPythonUtf8) {
        [Environment]::SetEnvironmentVariable("PYTHONUTF8", $previousPythonUtf8, "Process")
    }
    else {
        [Environment]::SetEnvironmentVariable("PYTHONUTF8", $null, "Process")
    }
}

exit $exitCode
