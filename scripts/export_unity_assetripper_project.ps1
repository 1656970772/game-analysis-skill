param(
    [Parameter(Mandatory = $true)][string]$SourcePath,
    [Parameter(Mandatory = $true)][string]$StudyRoot,
    [string]$SkillRoot = "",
    [int]$Port = 8177,
    [int]$StartupTimeoutSeconds = 90,
    [int]$RequestTimeoutSeconds = 3600,
    [string]$AsciiWorkRoot = "C:\CodexTemp\GameAnalysis_AssetRipper",
    [switch]$PrimaryContentFallback,
    [switch]$KeepServerAlive
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8

if ([string]::IsNullOrWhiteSpace($SkillRoot)) {
    $SkillRoot = Split-Path -Parent $PSScriptRoot
}

if (-not (Test-Path -LiteralPath $SourcePath)) {
    throw "SourcePath does not exist: $SourcePath"
}
if (-not (Test-Path -LiteralPath $StudyRoot -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $StudyRoot | Out-Null
}

$skillRootPath = (Resolve-Path -LiteralPath $SkillRoot).Path
$studyRootPath = (Resolve-Path -LiteralPath $StudyRoot).Path
$sourceResolved = (Resolve-Path -LiteralPath $SourcePath).Path
$assetRipper = Join-Path $skillRootPath "tools\AssetRipper_1.3.14\AssetRipper.GUI.Free.exe"
if (-not (Test-Path -LiteralPath $assetRipper -PathType Leaf)) {
    throw "AssetRipper executable was not found: $assetRipper"
}

function Ensure-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }
}

function Join-UnicodeChars {
    param([Parameter(Mandatory = $true)][int[]]$CodePoints)
    return -join ($CodePoints | ForEach-Object { [char]$_ })
}

$rawExportDirName = "0." + (Join-UnicodeChars -CodePoints @(0x539f, 0x59cb, 0x5bfc, 0x51fa))
$tempDirName = "4." + (Join-UnicodeChars -CodePoints @(0x4e34, 0x65f6, 0x76ee, 0x5f55))
$toolLogsDirName = Join-UnicodeChars -CodePoints @(0x5de5, 0x5177, 0x65e5, 0x5fd7)

$logDir = Join-Path (Join-Path (Join-Path $studyRootPath $tempDirName) $toolLogsDirName) "AssetRipper"
$rawRoot = Join-Path (Join-Path $studyRootPath $rawExportDirName) "AssetRipper"
$exportRequestPath = $rawRoot
$exportPath = Join-Path $exportRequestPath "ExportedProject"
Ensure-Directory -Path $logDir
Ensure-Directory -Path $exportRequestPath

$assetRipperLog = Join-Path $logDir "assetripper.log"
$safeStudyName = ([IO.Path]::GetFileName($studyRootPath) -replace "[^A-Za-z0-9_.-]", "_")
if ([string]::IsNullOrWhiteSpace($safeStudyName)) {
    $safeStudyName = "study"
}
$assetRipperRuntimeDir = Join-Path $AsciiWorkRoot $safeStudyName
$assetRipperRuntimeLog = Join-Path $assetRipperRuntimeDir "assetripper.log"
$openApiPath = Join-Path $logDir "assetripper-openapi.json"
$apiLogPath = Join-Path $logDir "assetripper-api-log.json"
$baseUrl = "http://127.0.0.1:$Port"
$events = [System.Collections.Generic.List[object]]::new()
Ensure-Directory -Path $assetRipperRuntimeDir

function Add-Event {
    param(
        [string]$Stage,
        [string]$Status,
        [hashtable]$Data = @{}
    )
    $payload = [ordered]@{
        time = [DateTimeOffset]::Now.ToString("o")
        stage = $Stage
        status = $Status
    }
    foreach ($key in $Data.Keys) {
        $payload[$key] = $Data[$key]
    }
    $events.Add([pscustomobject]$payload) | Out-Null
}

function Save-ApiLog {
    Ensure-Directory -Path (Split-Path -Parent $apiLogPath)
    $events | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $apiLogPath -Encoding UTF8
}

function Copy-AssetRipperRuntimeLog {
    if (Test-Path -LiteralPath $assetRipperRuntimeLog -PathType Leaf) {
        Copy-Item -LiteralPath $assetRipperRuntimeLog -Destination $assetRipperLog -Force
    }
}

function Invoke-LocalApiWebRequest {
    param([Parameter(Mandatory = $true)][hashtable]$Request)
    return Invoke-WebRequest @Request -NoProxy
}

function Invoke-LoggedWebRequest {
    param(
        [string]$Stage,
        [string]$Uri,
        [string]$Method = "Get",
        [hashtable]$Body = @{}
    )
    try {
        $request = @{
            Uri = $Uri
            Method = $Method
            TimeoutSec = $RequestTimeoutSeconds
            UseBasicParsing = $true
        }
        if ($Body.Count -gt 0) {
            $request["Body"] = $Body
        }
        $response = Invoke-LocalApiWebRequest -Request $request
        Add-Event -Stage $Stage -Status "ok" -Data @{
            uri = $Uri
            status_code = [int]$response.StatusCode
            response_length = [int]$response.Content.Length
        }
        return $response
    }
    catch {
        $detail = $_.Exception.Message
        if ($_.Exception.Response -and $_.Exception.Response.GetResponseStream()) {
            try {
                $stream = $_.Exception.Response.GetResponseStream()
                $reader = New-Object System.IO.StreamReader($stream)
                $detail = $reader.ReadToEnd()
            }
            catch {
                $detail = $_.Exception.Message
            }
        }
        Add-Event -Stage $Stage -Status "failed" -Data @{
            uri = $Uri
            error = $detail
        }
        Save-ApiLog
        throw "AssetRipper API stage failed [$Stage]: $detail"
    }
}

$proc = $null
try {
    $proc = Start-Process -FilePath $assetRipper -ArgumentList @(
        "--headless",
        "--port",
        [string]$Port,
        "--log",
        "--log-path",
        $assetRipperRuntimeLog
    ) -WorkingDirectory (Split-Path -Parent $assetRipper) -WindowStyle Hidden -PassThru
    Add-Event -Stage "start" -Status "ok" -Data @{
        pid = [int]$proc.Id
        exe = $assetRipper
        port = [int]$Port
        log_path = $assetRipperLog
        runtime_log_path = $assetRipperRuntimeLog
    }

    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $openApi = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $openApi = Invoke-LocalApiWebRequest -Request @{
                Uri = "$baseUrl/openapi.json"
                UseBasicParsing = $true
                TimeoutSec = 5
            }
            break
        }
        catch {
            Start-Sleep -Milliseconds 750
        }
    }
    if ($null -eq $openApi) {
        Add-Event -Stage "openapi" -Status "failed" -Data @{ uri = "$baseUrl/openapi.json"; error = "startup_timeout" }
        Save-ApiLog
        throw "AssetRipper did not expose openapi.json within $StartupTimeoutSeconds seconds."
    }
    $openApi.Content | Set-Content -LiteralPath $openApiPath -Encoding UTF8
    Add-Event -Stage "openapi" -Status "ok" -Data @{
        uri = "$baseUrl/openapi.json"
        path = $openApiPath
        status_code = [int]$openApi.StatusCode
    }

    $sourceItem = Get-Item -LiteralPath $sourceResolved
    if ($sourceItem.PSIsContainer) {
        Invoke-LoggedWebRequest -Stage "load_folder" -Uri "$baseUrl/LoadFolder" -Method "Post" -Body @{ path = $sourceResolved } | Out-Null
    } else {
        Invoke-LoggedWebRequest -Stage "load_file" -Uri "$baseUrl/LoadFile" -Method "Post" -Body @{ path = $sourceResolved } | Out-Null
    }

    try {
        Invoke-LoggedWebRequest -Stage "export_unity_project" -Uri "$baseUrl/Export/UnityProject" -Method "Post" -Body @{ path = $exportRequestPath } | Out-Null
    }
    catch {
        if (-not $PrimaryContentFallback) {
            throw
        }
        Add-Event -Stage "export_unity_project" -Status "fallback" -Data @{ fallback = "Export/PrimaryContent" }
        Invoke-LoggedWebRequest -Stage "export_primary_content" -Uri "$baseUrl/Export/PrimaryContent" -Method "Post" -Body @{ path = $exportRequestPath } | Out-Null
    }

    $exportedFiles = 0
    if (Test-Path -LiteralPath $exportPath -PathType Container) {
        $exportedFiles = (Get-ChildItem -LiteralPath $exportPath -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
    }
    Add-Event -Stage "complete" -Status "ok" -Data @{
        export_path = $exportPath
        exported_files = [int]$exportedFiles
    }
    Save-ApiLog
    [pscustomobject]@{
        status = "ok"
        source_path = $sourceResolved
        export_path = $exportPath
        exported_files = [int]$exportedFiles
        api_log = $apiLogPath
        assetripper_log = $assetRipperLog
        openapi = $openApiPath
        pid = [int]$proc.Id
    } | ConvertTo-Json -Depth 4
}
finally {
    Copy-AssetRipperRuntimeLog
    if (($null -ne $proc) -and (-not $KeepServerAlive)) {
        try {
            if (-not $proc.HasExited) {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                Add-Event -Stage "stop" -Status "ok" -Data @{ pid = [int]$proc.Id }
                Copy-AssetRipperRuntimeLog
                Save-ApiLog
            }
        }
        catch {
            Add-Event -Stage "stop" -Status "failed" -Data @{ error = $_.Exception.Message }
            Save-ApiLog
        }
    }
}
