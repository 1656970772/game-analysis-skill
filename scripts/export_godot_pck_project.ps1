param(
    [Parameter(Mandatory = $true)]
    [string]$SourcePath,

    [Parameter(Mandatory = $true)]
    [string]$StudyRoot,

    [string]$SkillRoot = "",

    [ValidateRange(1, 86400)]
    [int]$TimeoutSeconds = 600,

    [switch]$ScriptsOnly,

    [string]$ForceBytecodeVersion = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8

function ConvertFrom-CodePoints {
    param([Parameter(Mandatory = $true)][int[]]$CodePoints)
    return -join ($CodePoints | ForEach-Object { [char]$_ })
}

function Assert-PathWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $candidateFull = [GameAnalysis.PathCanonicalizer]::Canonicalize($Candidate).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $rootFull = [GameAnalysis.PathCanonicalizer]::Canonicalize($Root).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $rootFull + [IO.Path]::DirectorySeparatorChar
    if (-not $candidateFull.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name is outside StudyRoot: $candidateFull"
    }
    return $candidateFull
}

function Quote-ProcessArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    if ($Value.Contains('"')) {
        throw "Process arguments containing a double quote are not supported."
    }
    return '"' + $Value + '"'
}

if ($null -eq ("GameAnalysis.GdreProcessJob" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace GameAnalysis {
    public static class PathCanonicalizer {
        private const UInt32 INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF;
        private const UInt32 FILE_SHARE_READ = 0x00000001;
        private const UInt32 FILE_SHARE_WRITE = 0x00000002;
        private const UInt32 FILE_SHARE_DELETE = 0x00000004;
        private const UInt32 OPEN_EXISTING = 3;
        private const UInt32 FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
        private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern UInt32 GetFileAttributesW(string path);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFileW(
            string fileName,
            UInt32 desiredAccess,
            UInt32 shareMode,
            IntPtr securityAttributes,
            UInt32 creationDisposition,
            UInt32 flagsAndAttributes,
            IntPtr templateFile
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern UInt32 GetFinalPathNameByHandleW(
            IntPtr file,
            StringBuilder path,
            UInt32 pathLength,
            UInt32 flags
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);

        public static string Canonicalize(string path) {
            string full = Path.GetFullPath(path);
            string probe = full;
            var missingParts = new System.Collections.Generic.Stack<string>();
            while (GetFileAttributesW(probe) == INVALID_FILE_ATTRIBUTES) {
                string leaf = Path.GetFileName(probe);
                string parent = Path.GetDirectoryName(probe);
                if (String.IsNullOrEmpty(leaf) || String.IsNullOrEmpty(parent) || parent == probe) {
                    throw new IOException("Cannot find an existing ancestor for path: " + full);
                }
                missingParts.Push(leaf);
                probe = parent;
            }

            IntPtr handle = CreateFileW(
                probe,
                0,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                IntPtr.Zero,
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS,
                IntPtr.Zero
            );
            if (handle == INVALID_HANDLE_VALUE) {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot canonicalize path: " + probe);
            }
            try {
                var builder = new StringBuilder(32768);
                UInt32 length = GetFinalPathNameByHandleW(handle, builder, (UInt32)builder.Capacity, 0);
                if (length == 0) {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot resolve final path: " + probe);
                }
                if (length >= builder.Capacity) {
                    builder = new StringBuilder((Int32)length + 1);
                    length = GetFinalPathNameByHandleW(handle, builder, (UInt32)builder.Capacity, 0);
                    if (length == 0 || length >= builder.Capacity) {
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot resolve final path: " + probe);
                    }
                }
                string canonical = builder.ToString();
                if (canonical.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) {
                    canonical = @"\\" + canonical.Substring(8);
                }
                else if (canonical.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase)) {
                    canonical = canonical.Substring(4);
                }
                while (missingParts.Count > 0) {
                    canonical = Path.Combine(canonical, missingParts.Pop());
                }
                return Path.GetFullPath(canonical);
            }
            finally {
                CloseHandle(handle);
            }
        }
    }

    public sealed class GdreProcessJob {
        private const UInt32 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
        private const Int32 JobObjectExtendedLimitInformation = 9;
        private const UInt32 CREATE_SUSPENDED = 0x00000004;
        private const UInt32 CREATE_NO_WINDOW = 0x08000000;
        private const UInt32 STARTF_USESTDHANDLES = 0x00000100;
        private const UInt32 GENERIC_WRITE = 0x40000000;
        private const UInt32 FILE_SHARE_READ = 0x00000001;
        private const UInt32 CREATE_ALWAYS = 2;
        private const UInt32 FILE_ATTRIBUTE_NORMAL = 0x00000080;
        private const Int32 STD_INPUT_HANDLE = -10;
        private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);
        private IntPtr handle;
        private bool wasAssigned;
        private bool wasResumed;
        private string launchStage;

        public bool WasAssigned { get { return wasAssigned; } }
        public bool WasResumed { get { return wasResumed; } }
        public string LaunchStage { get { return launchStage; } }

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS {
            public UInt64 ReadOperationCount;
            public UInt64 WriteOperationCount;
            public UInt64 OtherOperationCount;
            public UInt64 ReadTransferCount;
            public UInt64 WriteTransferCount;
            public UInt64 OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
            public Int64 PerProcessUserTimeLimit;
            public Int64 PerJobUserTimeLimit;
            public UInt32 LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public UInt32 ActiveProcessLimit;
            public UIntPtr Affinity;
            public UInt32 PriorityClass;
            public UInt32 SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct SECURITY_ATTRIBUTES {
            public Int32 nLength;
            public IntPtr lpSecurityDescriptor;
            [MarshalAs(UnmanagedType.Bool)]
            public bool bInheritHandle;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct STARTUPINFO {
            public Int32 cb;
            public string lpReserved;
            public string lpDesktop;
            public string lpTitle;
            public UInt32 dwX;
            public UInt32 dwY;
            public UInt32 dwXSize;
            public UInt32 dwYSize;
            public UInt32 dwXCountChars;
            public UInt32 dwYCountChars;
            public UInt32 dwFillAttribute;
            public UInt32 dwFlags;
            public UInt16 wShowWindow;
            public UInt16 cbReserved2;
            public IntPtr lpReserved2;
            public IntPtr hStdInput;
            public IntPtr hStdOutput;
            public IntPtr hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct PROCESS_INFORMATION {
            public IntPtr hProcess;
            public IntPtr hThread;
            public UInt32 dwProcessId;
            public UInt32 dwThreadId;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr securityAttributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(
            IntPtr job,
            Int32 informationClass,
            IntPtr information,
            UInt32 informationLength
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CreateProcess(
            string applicationName,
            StringBuilder commandLine,
            IntPtr processAttributes,
            IntPtr threadAttributes,
            [MarshalAs(UnmanagedType.Bool)] bool inheritHandles,
            UInt32 creationFlags,
            IntPtr environment,
            string currentDirectory,
            ref STARTUPINFO startupInfo,
            out PROCESS_INFORMATION processInformation
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFile(
            string fileName,
            UInt32 desiredAccess,
            UInt32 shareMode,
            ref SECURITY_ATTRIBUTES securityAttributes,
            UInt32 creationDisposition,
            UInt32 flagsAndAttributes,
            IntPtr templateFile
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern UInt32 ResumeThread(IntPtr thread);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool TerminateProcess(IntPtr process, UInt32 exitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern UInt32 WaitForSingleObject(IntPtr handle, UInt32 milliseconds);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr GetStdHandle(Int32 standardHandle);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr value);

        public GdreProcessJob() {
            launchStage = "creating_job";
            handle = CreateJobObject(IntPtr.Zero, null);
            if (handle == IntPtr.Zero) {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateJobObject failed");
            }
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            Int32 length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
            IntPtr pointer = Marshal.AllocHGlobal(length);
            try {
                Marshal.StructureToPtr(limits, pointer, false);
                if (!SetInformationJobObject(handle, JobObjectExtendedLimitInformation, pointer, (UInt32)length)) {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "SetInformationJobObject failed");
                }
            }
            catch {
                CloseHandle(handle);
                handle = IntPtr.Zero;
                throw;
            }
            finally {
                Marshal.FreeHGlobal(pointer);
            }
            launchStage = "job_ready";
        }

        private static string QuoteExecutable(string value) {
            if (value.IndexOf('"') >= 0) {
                throw new ArgumentException("Executable paths containing a double quote are not supported.");
            }
            return "\"" + value + "\"";
        }

        public Process StartAssignedProcess(
            string executable,
            string arguments,
            string workingDirectory,
            string stdoutPath,
            string stderrPath
        ) {
            if (handle == IntPtr.Zero) {
                throw new InvalidOperationException("Job object is already closed.");
            }
            if (String.IsNullOrWhiteSpace(executable)) {
                throw new ArgumentException("Executable path is required.");
            }

            SECURITY_ATTRIBUTES security = new SECURITY_ATTRIBUTES();
            security.nLength = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES));
            security.bInheritHandle = true;
            IntPtr stdoutHandle = INVALID_HANDLE_VALUE;
            IntPtr stderrHandle = INVALID_HANDLE_VALUE;
            PROCESS_INFORMATION nativeProcess = new PROCESS_INFORMATION();
            Process managedProcess = null;
            bool created = false;

            try {
                stdoutHandle = CreateFile(
                    stdoutPath,
                    GENERIC_WRITE,
                    FILE_SHARE_READ,
                    ref security,
                    CREATE_ALWAYS,
                    FILE_ATTRIBUTE_NORMAL,
                    IntPtr.Zero
                );
                if (stdoutHandle == INVALID_HANDLE_VALUE) {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not create the stdout log");
                }
                stderrHandle = CreateFile(
                    stderrPath,
                    GENERIC_WRITE,
                    FILE_SHARE_READ,
                    ref security,
                    CREATE_ALWAYS,
                    FILE_ATTRIBUTE_NORMAL,
                    IntPtr.Zero
                );
                if (stderrHandle == INVALID_HANDLE_VALUE) {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not create the stderr log");
                }

                string launchExecutable = executable;
                string commandLine;
                string extension = Path.GetExtension(executable);
                if (String.Equals(extension, ".cmd", StringComparison.OrdinalIgnoreCase) ||
                    String.Equals(extension, ".bat", StringComparison.OrdinalIgnoreCase)) {
                    launchExecutable = Environment.GetEnvironmentVariable("ComSpec");
                    if (String.IsNullOrWhiteSpace(launchExecutable)) {
                        launchExecutable = Path.Combine(Environment.SystemDirectory, "cmd.exe");
                    }
                    commandLine = QuoteExecutable(launchExecutable) + " /D /S /C \"" +
                        QuoteExecutable(executable) + " " + arguments + "\"";
                }
                else {
                    commandLine = QuoteExecutable(executable) + " " + arguments;
                }

                STARTUPINFO startup = new STARTUPINFO();
                startup.cb = Marshal.SizeOf(typeof(STARTUPINFO));
                startup.dwFlags = STARTF_USESTDHANDLES;
                startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
                startup.hStdOutput = stdoutHandle;
                startup.hStdError = stderrHandle;
                launchStage = "creating_process_suspended";
                StringBuilder mutableCommandLine = new StringBuilder(commandLine);
                if (!CreateProcess(
                    launchExecutable,
                    mutableCommandLine,
                    IntPtr.Zero,
                    IntPtr.Zero,
                    true,
                    CREATE_SUSPENDED | CREATE_NO_WINDOW,
                    IntPtr.Zero,
                    workingDirectory,
                    ref startup,
                    out nativeProcess
                )) {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateProcess failed");
                }
                created = true;
                launchStage = "process_created_suspended";

                if (!AssignProcessToJobObject(handle, nativeProcess.hProcess)) {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "AssignProcessToJobObject failed");
                }
                wasAssigned = true;
                launchStage = "process_assigned_suspended";
                managedProcess = Process.GetProcessById((Int32)nativeProcess.dwProcessId);

                if (ResumeThread(nativeProcess.hThread) == UInt32.MaxValue) {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "ResumeThread failed");
                }
                wasResumed = true;
                launchStage = "process_resumed";
                return managedProcess;
            }
            catch {
                launchStage = launchStage + "_failed";
                if (created && nativeProcess.hProcess != IntPtr.Zero) {
                    TerminateProcess(nativeProcess.hProcess, 1);
                    WaitForSingleObject(nativeProcess.hProcess, 5000);
                }
                if (managedProcess != null) {
                    managedProcess.Dispose();
                }
                throw;
            }
            finally {
                if (nativeProcess.hThread != IntPtr.Zero) {
                    CloseHandle(nativeProcess.hThread);
                }
                if (nativeProcess.hProcess != IntPtr.Zero) {
                    CloseHandle(nativeProcess.hProcess);
                }
                if (stdoutHandle != INVALID_HANDLE_VALUE) {
                    CloseHandle(stdoutHandle);
                }
                if (stderrHandle != INVALID_HANDLE_VALUE) {
                    CloseHandle(stderrHandle);
                }
            }
        }

        public bool CloseAndKill() {
            if (handle == IntPtr.Zero) {
                return true;
            }
            bool result = CloseHandle(handle);
            handle = IntPtr.Zero;
            return result;
        }
    }
}
"@
}

if ([string]::IsNullOrWhiteSpace($SkillRoot)) {
    if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        throw "SkillRoot is required when PSScriptRoot is unavailable."
    }
    $SkillRoot = Split-Path -Parent $PSScriptRoot
}

$resolvedSource = [GameAnalysis.PathCanonicalizer]::Canonicalize((Resolve-Path -LiteralPath $SourcePath).Path)
$resolvedStudyRoot = [GameAnalysis.PathCanonicalizer]::Canonicalize((Resolve-Path -LiteralPath $StudyRoot).Path)
$resolvedSkillRoot = [GameAnalysis.PathCanonicalizer]::Canonicalize((Resolve-Path -LiteralPath $SkillRoot).Path)
if (-not (Test-Path -LiteralPath $resolvedSource -PathType Leaf)) {
    throw "SourcePath is not a file: $resolvedSource"
}
if ([IO.Path]::GetExtension($resolvedSource) -ine ".pck") {
    throw "SourcePath must be a standalone .pck file; game executables are never launched by this wrapper."
}

$manifestPath = Join-Path $resolvedSkillRoot "references\tooling-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Tool manifest is missing: $manifestPath"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$toolRecord = $manifest.tools | Where-Object { $_.name -eq "GDRETools" } | Select-Object -First 1
if ($null -eq $toolRecord) {
    throw "GDRETools is not declared in tooling-manifest.json."
}
$toolRelative = ([string]$toolRecord.local_path).Replace('/', [IO.Path]::DirectorySeparatorChar)
$toolCandidate = Join-Path $resolvedSkillRoot $toolRelative
$gdreExe = Assert-PathWithinRoot -Candidate $toolCandidate -Root $resolvedSkillRoot -Name "GDRETools"
if (-not (Test-Path -LiteralPath $gdreExe -PathType Leaf)) {
    throw "GDRETools executable is missing: $gdreExe"
}
$toolHash = (Get-FileHash -LiteralPath $gdreExe -Algorithm SHA256).Hash.ToUpperInvariant()
$expectedToolHash = [string]$toolRecord.exe_sha256
if ($expectedToolHash -notmatch '^[0-9A-Fa-f]{64}$') {
    throw "GDRETools exe_sha256 is missing or malformed; it must be exactly 64 hexadecimal characters in tooling-manifest.json."
}
$expectedToolHash = $expectedToolHash.ToUpperInvariant()
if ($toolHash -ne $expectedToolHash) {
    throw "GDRETools SHA256 mismatch: expected=$expectedToolHash actual=$toolHash"
}

# Avoid non-ASCII string literals so Windows PowerShell 5.1 can parse this file without a BOM.
$rawExportName = "0." + (ConvertFrom-CodePoints @(0x539F, 0x59CB, 0x5BFC, 0x51FA))
$recoveryName = "GDRETools" + (ConvertFrom-CodePoints @(0x6062, 0x590D))
$temporaryName = "4." + (ConvertFrom-CodePoints @(0x4E34, 0x65F6, 0x76EE, 0x5F55))
$sampleName = ConvertFrom-CodePoints @(0x5C0F, 0x6837, 0x672C)
$scriptRecoveryName = "GDRETools" + (ConvertFrom-CodePoints @(0x811A, 0x672C, 0x6062, 0x590D))
$toolLogName = ConvertFrom-CodePoints @(0x5DE5, 0x5177, 0x65E5, 0x5FD7)
$middleIndexName = ConvertFrom-CodePoints @(0x4E2D, 0x95F4, 0x7D22, 0x5F15)

$fullOutputCandidate = Join-Path (Join-Path $resolvedStudyRoot $rawExportName) $recoveryName
$scriptsOutputCandidate = Join-Path (Join-Path (Join-Path $resolvedStudyRoot $temporaryName) $sampleName) $scriptRecoveryName
$outputRoot = if ($ScriptsOnly) { $scriptsOutputCandidate } else { $fullOutputCandidate }
$outputRoot = Assert-PathWithinRoot -Candidate $outputRoot -Root $resolvedStudyRoot -Name "GDRETools output"

$logRootCandidate = Join-Path (Join-Path (Join-Path $resolvedStudyRoot $temporaryName) $toolLogName) "GDRETools"
$indexRootCandidate = Join-Path (Join-Path (Join-Path $resolvedStudyRoot $temporaryName) $middleIndexName) "Godot"
$logRoot = Assert-PathWithinRoot -Candidate $logRootCandidate -Root $resolvedStudyRoot -Name "GDRETools log root"
$indexRoot = Assert-PathWithinRoot -Candidate $indexRootCandidate -Root $resolvedStudyRoot -Name "Godot index root"
if (Test-Path -LiteralPath $outputRoot) {
    if (-not (Test-Path -LiteralPath $outputRoot -PathType Container)) {
        throw "GDRETools output exists and is not a directory: $outputRoot"
    }
    $existingOutput = @(Get-ChildItem -LiteralPath $outputRoot -Force | Select-Object -First 1)
    if ($existingOutput.Count -gt 0) {
        throw "GDRETools output already exists and is not empty; refusing an implicit resume or overwrite: $outputRoot"
    }
}
else {
    New-Item -ItemType Directory -Path $outputRoot | Out-Null
}
New-Item -ItemType Directory -Force -Path $logRoot, $indexRoot | Out-Null

$mode = if ($ScriptsOnly) { "scripts" } else { "full" }
$stdoutPath = Join-Path $logRoot ("recover-" + $mode + ".stdout.log")
$stderrPath = Join-Path $logRoot ("recover-" + $mode + ".stderr.log")
$summaryName = if ($ScriptsOnly) { "gdre_scripts_execution_summary.json" } else { "gdre_full_execution_summary.json" }
$summaryPath = Join-Path $indexRoot $summaryName

$arguments = New-Object 'System.Collections.Generic.List[string]'
$arguments.Add("--headless")
$arguments.Add("--recover=$resolvedSource")
$arguments.Add("--output=$outputRoot")
if ($ScriptsOnly) {
    $arguments.Add("--scripts-only")
}
if (-not [string]::IsNullOrWhiteSpace($ForceBytecodeVersion)) {
    $arguments.Add("--force-bytecode-version=$ForceBytecodeVersion")
}
$argumentLine = ($arguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " "

$sourceHashBefore = (Get-FileHash -LiteralPath $resolvedSource -Algorithm SHA256).Hash.ToUpperInvariant()
$startedUtc = [DateTime]::UtcNow
$process = $null
$exitCode = $null
$timedOut = $false
$failure = $null
$sourceHashAfter = $null
$sourceUnchanged = $false
$job = $null
$jobAssigned = $false
$processResumed = $false
$processId = $null
$launchStage = "not_started"
$jobClosed = $false
$cleanupGuaranteed = $false
$cleanupFailure = $null

try {
    $job = New-Object GameAnalysis.GdreProcessJob
    $process = $job.StartAssignedProcess(
        $gdreExe,
        $argumentLine,
        (Split-Path -Parent $gdreExe),
        $stdoutPath,
        $stderrPath
    )
    $jobAssigned = $job.WasAssigned
    $processResumed = $job.WasResumed
    $processId = $process.Id
    $launchStage = $job.LaunchStage

    $completed = $process.WaitForExit($TimeoutSeconds * 1000)
    if (-not $completed) {
        $timedOut = $true
        if (-not $job.CloseAndKill()) {
            $cleanupFailure = "CloseHandle failed while terminating the GDRETools Job Object."
            throw $cleanupFailure
        }
        $jobClosed = $true
        $cleanupGuaranteed = $true
        if (-not $process.WaitForExit(5000)) {
            $cleanupFailure = "GDRETools root process remained alive after closing the kill-on-close Job Object."
            throw $cleanupFailure
        }
        throw "GDRETools timed out after $TimeoutSeconds seconds."
    }
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    if (-not $job.CloseAndKill()) {
        $cleanupFailure = "CloseHandle failed while closing the GDRETools Job Object."
        throw $cleanupFailure
    }
    $jobClosed = $true
    $cleanupGuaranteed = $true
    if ($exitCode -ne 0) {
        throw "GDRETools recovery failed with exit code $exitCode. See $stdoutPath and $stderrPath."
    }
}
catch {
    if ($null -ne $job) {
        $jobAssigned = $job.WasAssigned
        $processResumed = $job.WasResumed
        $launchStage = $job.LaunchStage
    }
    $failure = $_.Exception.Message
}
finally {
    if ($null -ne $job -and -not $jobClosed) {
        if ($job.CloseAndKill()) {
            $jobClosed = $true
            $cleanupGuaranteed = $jobAssigned
        }
        else {
            $cleanupFailure = "CloseHandle failed while cleaning the GDRETools Job Object."
        }
    }
    if ($null -ne $process -and -not $process.HasExited) {
        $process.WaitForExit(5000) | Out-Null
    }
    if ($null -ne $process -and -not $process.HasExited) {
        $cleanupFailure = "GDRETools root process remained alive; process-tree cleanup is not guaranteed."
    }
    if ($null -ne $cleanupFailure) {
        if ([string]::IsNullOrWhiteSpace($failure)) {
            $failure = $cleanupFailure
        }
        elseif (-not $failure.Contains($cleanupFailure)) {
            $failure = $failure + " Cleanup failure: " + $cleanupFailure
        }
    }
    $sourceHashAfter = (Get-FileHash -LiteralPath $resolvedSource -Algorithm SHA256).Hash.ToUpperInvariant()
    $sourceUnchanged = $sourceHashBefore -eq $sourceHashAfter
    $summary = [ordered]@{
        schema_version = 1
        mode = $mode
        tool = [ordered]@{
            name = "GDRETools"
            version = [string]$toolRecord.version
            path = $gdreExe
            sha256 = $toolHash
        }
        source = $resolvedSource
        output = $outputRoot
        argument_line = $argumentLine
        timeout_seconds = $TimeoutSeconds
        timed_out = $timedOut
        launch_strategy = "create-suspended-assign-resume"
        launch_stage = $launchStage
        process_id = $processId
        job_object_assigned = $jobAssigned
        process_resumed = $processResumed
        cleanup_guaranteed = $cleanupGuaranteed
        cleanup_failure = $cleanupFailure
        exit_code = $exitCode
        started_utc = $startedUtc.ToString("o")
        finished_utc = [DateTime]::UtcNow.ToString("o")
        source_sha256_before = $sourceHashBefore
        source_sha256_after = $sourceHashAfter
        source_unchanged = $sourceUnchanged
        stdout_log = $stdoutPath
        stderr_log = $stderrPath
        failure = $failure
    }
    $json = $summary | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($summaryPath, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

if (-not $sourceUnchanged) {
    throw "Source PCK SHA256 changed during GDRETools execution. See $summaryPath."
}
if ($null -ne $failure) {
    throw "$failure Summary: $summaryPath"
}

Write-Output ($summary | ConvertTo-Json -Depth 8)
