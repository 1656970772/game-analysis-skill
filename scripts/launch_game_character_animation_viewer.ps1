param(
    [Parameter(Mandatory = $true)][string]$ViewerPath,
    [Parameter(Mandatory = $true)][string]$GameRoot,
    [Parameter(Mandatory = $true)][string]$LogPath
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8

function Write-LaunchLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    $directory = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($LogPath))
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    # The generated BAT also redirects stdout/stderr to this file. Use an
    # explicitly shared append handle so Windows PowerShell 5.1 and pwsh can
    # add structured lines while cmd.exe owns the redirect handle.
    try {
        $stream = New-Object IO.FileStream(
            $LogPath,
            [IO.FileMode]::Append,
            [IO.FileAccess]::Write,
            [IO.FileShare]::ReadWrite
        )
        try {
            $writer = New-Object IO.StreamWriter($stream, (New-Object Text.UTF8Encoding($false)))
            try {
                $writer.WriteLine(("[{0:O}] {1}" -f [DateTime]::UtcNow, $Message))
                $writer.Flush()
            }
            finally {
                $writer.Dispose()
            }
        }
        finally {
            if ($null -ne $stream) {
                $stream.Dispose()
            }
        }
    }
    catch [IO.IOException] {
        # An older generated BAT may own this file as its stdout/stderr
        # redirect. Write-Host/Write-Error is already captured by that handle,
        # so a structured duplicate must not prevent the viewer from starting.
    }
}

function Find-InstalledViewerProcess {
    param([Parameter(Mandatory = $true)][string]$ResolvedViewer)

    foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
        try {
            if ($process.Path -and $process.Path.Equals($ResolvedViewer, [StringComparison]::OrdinalIgnoreCase)) {
                return $process
            }
        }
        catch {
            # Protected/system processes may deny Path access; they cannot be this user-owned viewer.
        }
    }
    return $null
}

function Request-WindowFocus {
    param([Parameter(Mandatory = $true)]$Process)

    if (-not ("GameAnalysisWindowActivation" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class GameAnalysisWindowActivation {
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
}
"@
    }
    $Process.Refresh()
    if ($Process.MainWindowHandle -eq [IntPtr]::Zero) {
        return $false
    }
    [void][GameAnalysisWindowActivation]::ShowWindowAsync($Process.MainWindowHandle, 9)
    return [GameAnalysisWindowActivation]::SetForegroundWindow($Process.MainWindowHandle)
}

try {
    $resolvedViewer = (Resolve-Path -LiteralPath $ViewerPath -ErrorAction Stop).ProviderPath
    $resolvedGameRoot = (Resolve-Path -LiteralPath $GameRoot -ErrorAction Stop).ProviderPath
    $existing = Find-InstalledViewerProcess -ResolvedViewer $resolvedViewer
    if ($existing) {
        $focused = Request-WindowFocus -Process $existing
        $message = "Viewer is already running (PID $($existing.Id)); focus requested: $focused."
        Write-Host $message
        Write-LaunchLog -Message $message
        exit 0
    }

    $env:GAME_ANALYSIS_DST_ROOT = $resolvedGameRoot
    $message = "Starting character viewer from '$resolvedViewer' with authorized DST root '$resolvedGameRoot'."
    Write-Host $message
    Write-LaunchLog -Message $message
    $viewerDirectory = [IO.Path]::GetDirectoryName($resolvedViewer)
    $process = Start-Process -FilePath $resolvedViewer -WorkingDirectory $viewerDirectory -PassThru -Wait
    $exitMessage = "Viewer process exited with code $($process.ExitCode)."
    Write-Host $exitMessage
    Write-LaunchLog -Message $exitMessage
    exit $process.ExitCode
}
catch {
    $message = "Character viewer launch failed: $($_.Exception.Message)"
    Write-Error $message
    try { Write-LaunchLog -Message $message } catch { }
    exit 1
}
