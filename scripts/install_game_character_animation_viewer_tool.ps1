param(
    [string]$SkillRoot = "",
    [string]$ConfigPath = "",
    [string]$SourceRoot = "",
    [string]$ArtifactPath = "",
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8

function Resolve-GameAnalysisSkillRoot {
    param([string]$ProvidedSkillRoot)

    if (-not [string]::IsNullOrWhiteSpace($ProvidedSkillRoot)) {
        return (Resolve-Path -LiteralPath $ProvidedSkillRoot -ErrorAction Stop).ProviderPath
    }
    if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        throw "Cannot infer the GameAnalysis skill root. Pass -SkillRoot explicitly."
    }
    return (Resolve-Path -LiteralPath (Split-Path -Path $PSScriptRoot -Parent) -ErrorAction Stop).ProviderPath
}

function Assert-PathInside {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ParentPath,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    $fullParent = [IO.Path]::GetFullPath($ParentPath)
    $prefix = $fullParent
    if (-not $prefix.EndsWith([IO.Path]::DirectorySeparatorChar)) {
        $prefix += [IO.Path]::DirectorySeparatorChar
    }
    if (
        -not $fullPath.Equals($fullParent, [StringComparison]::OrdinalIgnoreCase) -and
        -not $fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "$Label escapes its allowed root '$fullParent': '$fullPath'"
    }
}

function Resolve-SafeRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Relative,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ([string]::IsNullOrWhiteSpace($Relative) -or [IO.Path]::IsPathRooted($Relative)) {
        throw "$Label must be a non-empty relative path: '$Relative'"
    }
    $candidate = [IO.Path]::GetFullPath((Join-Path $Root $Relative))
    Assert-PathInside -Path $candidate -ParentPath $Root -Label $Label
    return $candidate
}

function Get-RequiredString {
    param(
        [Parameter(Mandatory = $true)]$Object,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Context
    )

    $value = $Object.$Name
    if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value)) {
        throw "$Context.$Name must be a non-empty string."
    }
    return $value
}

function Invoke-GitText {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )

    $git = Get-Command "git.exe" -ErrorAction SilentlyContinue
    if (-not $git) {
        $git = Get-Command "git" -ErrorAction SilentlyContinue
    }
    if (-not $git) {
        throw "git was not found; source commit provenance cannot be recorded."
    }
    $output = & $git.Source -C $Repository @Arguments 2>$null
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "git $($Arguments -join ' ') failed with exit code $exitCode for '$Repository'."
    }
    if ($exitCode -ne 0) {
        return ""
    }
    return (($output | Out-String).Trim())
}

function Find-Artifact {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)]$Config,
        [string]$ExplicitArtifact
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitArtifact)) {
        return (Resolve-Path -LiteralPath $ExplicitArtifact -ErrorAction Stop).ProviderPath
    }
    foreach ($relative in $Config.source_repository.artifact_candidates) {
        if ($relative -isnot [string] -or [string]::IsNullOrWhiteSpace($relative)) {
            throw "source_repository.artifact_candidates must contain only non-empty strings."
        }
        $candidate = Resolve-SafeRelativePath -Root $Repository -Relative $relative -Label "artifact candidate"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).ProviderPath
        }
    }
    throw "No AssetArchive build artifact was found. Build the derived viewer or pass -ArtifactPath explicitly."
}

function Invoke-ConfiguredBuild {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)]$BuildConfig
    )

    $commandName = Get-RequiredString -Object $BuildConfig -Name "command" -Context "build"
    $workingRelative = Get-RequiredString -Object $BuildConfig -Name "working_directory_relative" -Context "build"
    $workingDirectory = Resolve-SafeRelativePath -Root $Repository -Relative $workingRelative -Label "build working directory"
    if (-not (Test-Path -LiteralPath $workingDirectory -PathType Container)) {
        throw "Configured build working directory does not exist: '$workingDirectory'"
    }
    $command = Get-Command $commandName -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Configured build command was not found: '$commandName'"
    }
    if ($BuildConfig.arguments -isnot [Array]) {
        throw "build.arguments must be an array of strings."
    }
    $arguments = @()
    foreach ($argument in $BuildConfig.arguments) {
        if ($argument -isnot [string]) {
            throw "build.arguments must contain only strings."
        }
        $arguments += $argument
    }
    if ($null -eq $BuildConfig.process_environment) {
        throw "build.process_environment must be an object."
    }

    $savedEnvironment = @{}
    try {
        foreach ($property in $BuildConfig.process_environment.PSObject.Properties) {
            if ($property.Name -notmatch "^[A-Za-z_][A-Za-z0-9_]*$" -or $property.Value -isnot [string]) {
                throw "build.process_environment contains an invalid string environment entry."
            }
            $existing = Get-Item -LiteralPath ("Env:" + $property.Name) -ErrorAction SilentlyContinue
            $savedEnvironment[$property.Name] = [ordered]@{
                existed = $null -ne $existing
                value = if ($null -ne $existing) { $existing.Value } else { $null }
            }
            Set-Item -LiteralPath ("Env:" + $property.Name) -Value $property.Value
        }
        Push-Location -LiteralPath $workingDirectory
        try {
            & $command.Source @arguments
            if ($LASTEXITCODE -ne 0) {
                throw "$commandName $($arguments -join ' ') failed with exit code $LASTEXITCODE."
            }
        }
        finally {
            Pop-Location
        }
    }
    finally {
        foreach ($name in $savedEnvironment.Keys) {
            if ($savedEnvironment[$name].existed) {
                Set-Item -LiteralPath ("Env:" + $name) -Value $savedEnvironment[$name].value
            }
            else {
                Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue
            }
        }
    }
}

function Update-ToolingManifest {
    param(
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)]$Entry
    )

    $manifest = Get-Content -Raw -LiteralPath $ManifestPath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    if ($null -eq $manifest.tools) {
        throw "tooling-manifest.json does not contain a tools array."
    }
    $updated = New-Object System.Collections.Generic.List[object]
    $replaced = $false
    foreach ($tool in $manifest.tools) {
        if ($tool.name -eq $Entry.name) {
            $updated.Add($Entry)
            $replaced = $true
        }
        else {
            $updated.Add($tool)
        }
    }
    if (-not $replaced) {
        $updated.Add($Entry)
    }
    $manifest.tools = $updated.ToArray()
    $json = $manifest | ConvertTo-Json -Depth 30
    [IO.File]::WriteAllText($ManifestPath, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

$skillRootPath = Resolve-GameAnalysisSkillRoot -ProvidedSkillRoot $SkillRoot
$configCandidate = if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    Join-Path $skillRootPath "references\game-character-animation-viewer-config.json"
}
else {
    $ConfigPath
}
$configPathResolved = (Resolve-Path -LiteralPath $configCandidate -ErrorAction Stop).ProviderPath
$config = Get-Content -Raw -LiteralPath $configPathResolved -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop

if ($config.schema_version -ne 1 -or $null -eq $config.tool -or $null -eq $config.source_repository -or $null -eq $config.build) {
    throw "Unsupported or incomplete character viewer configuration: '$configPathResolved'"
}
$toolName = Get-RequiredString -Object $config.tool -Name "name" -Context "tool"
$version = Get-RequiredString -Object $config.tool -Name "version" -Context "tool"
$executableName = Get-RequiredString -Object $config.tool -Name "executable_name" -Context "tool"
$installRelative = Get-RequiredString -Object $config.tool -Name "install_relative" -Context "tool"
$licenseName = Get-RequiredString -Object $config.tool -Name "license_name" -Context "tool"
$originName = Get-RequiredString -Object $config.tool -Name "origin_name" -Context "tool"
$versionName = Get-RequiredString -Object $config.tool -Name "version_name" -Context "tool"
$licenseRelative = Get-RequiredString -Object $config.source_repository -Name "license_relative" -Context "source_repository"
$customCharacterResourcesRelative = $config.source_repository.custom_character_resources_relative
$customCharacterInstallRelative = $config.tool.custom_character_install_relative
if (
    ($customCharacterResourcesRelative -is [string] -and -not [string]::IsNullOrWhiteSpace($customCharacterResourcesRelative)) -xor
    ($customCharacterInstallRelative -is [string] -and -not [string]::IsNullOrWhiteSpace($customCharacterInstallRelative))
) {
    throw "Custom character resource source and install paths must be configured together."
}

$sourceCandidate = if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    Get-RequiredString -Object $config.source_repository -Name "default_path" -Context "source_repository"
}
else {
    $SourceRoot
}
$sourceRootPath = (Resolve-Path -LiteralPath $sourceCandidate -ErrorAction Stop).ProviderPath
if (-not (Test-Path -LiteralPath $sourceRootPath -PathType Container)) {
    throw "AssetArchive source root is not a directory: '$sourceRootPath'"
}

$buildEnvironment = [ordered]@{}
foreach ($property in $config.build.process_environment.PSObject.Properties) {
    if ($property.Value -isnot [string]) {
        throw "build.process_environment values must be strings."
    }
    $buildEnvironment[$property.Name] = $property.Value
}
if ($Build) {
    Invoke-ConfiguredBuild -Repository $sourceRootPath -BuildConfig $config.build
}

$artifact = Find-Artifact -Repository $sourceRootPath -Config $config -ExplicitArtifact $ArtifactPath
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "AssetArchive artifact is not a file: '$artifact'"
}
Assert-PathInside -Path $artifact -ParentPath $sourceRootPath -Label "AssetArchive artifact"
$licenseSource = Resolve-SafeRelativePath -Root $sourceRootPath -Relative $licenseRelative -Label "AssetArchive license"
if (-not (Test-Path -LiteralPath $licenseSource -PathType Leaf)) {
    throw "AssetArchive MIT license was not found: '$licenseSource'"
}
$customCharacterResourcesSource = $null
if ($customCharacterResourcesRelative -is [string] -and -not [string]::IsNullOrWhiteSpace($customCharacterResourcesRelative)) {
    $customCharacterResourcesSource = Resolve-SafeRelativePath `
        -Root $sourceRootPath `
        -Relative $customCharacterResourcesRelative `
        -Label "custom character resources"
    if (-not (Test-Path -LiteralPath $customCharacterResourcesSource -PathType Container)) {
        throw "Configured custom character resource directory does not exist: '$customCharacterResourcesSource'"
    }
}

$commit = Invoke-GitText -Repository $sourceRootPath -Arguments @("rev-parse", "HEAD")
if ($commit -notmatch "^[0-9a-fA-F]{40}$") {
    throw "AssetArchive source commit is invalid: '$commit'"
}
$remote = Invoke-GitText -Repository $sourceRootPath -Arguments @("config", "--get", "remote.origin.url") -AllowFailure
$dirty = -not [string]::IsNullOrWhiteSpace((Invoke-GitText -Repository $sourceRootPath -Arguments @("status", "--porcelain") -AllowFailure))

$builtInToolsRoot = Resolve-SafeRelativePath -Root $skillRootPath -Relative "built-in-tools" -Label "built-in tools root"
New-Item -ItemType Directory -Path $builtInToolsRoot -Force | Out-Null
$targetDir = Resolve-SafeRelativePath -Root $skillRootPath -Relative $installRelative -Label "character viewer install path"
Assert-PathInside -Path $targetDir -ParentPath $builtInToolsRoot -Label "character viewer install path"
$stageDir = Join-Path $builtInToolsRoot (".install-{0}-{1}" -f $toolName, [Guid]::NewGuid().ToString("N"))
Assert-PathInside -Path $stageDir -ParentPath $builtInToolsRoot -Label "character viewer stage path"

$artifactHash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToUpperInvariant()
$licenseHash = (Get-FileHash -LiteralPath $licenseSource -Algorithm SHA256).Hash.ToUpperInvariant()
$origin = [ordered]@{
    schema_version = 1
    tool = $toolName
    version = $version
    upstream_project = "AssetArchive-Dev"
    upstream_license = "MIT"
    source_repository = $sourceRootPath
    source_remote = $remote
    source_commit = $commit.ToLowerInvariant()
    source_dirty = $dirty
    build_invoked_by_installer = [bool]$Build
    build_process_environment = $buildEnvironment
    custom_character_resources = $customCharacterResourcesRelative
    source_artifact = $artifact
    artifact_sha256 = $artifactHash
    installed_executable = $executableName
    installed_executable_sha256 = $artifactHash
    license_sha256 = $licenseHash
}

try {
    New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
    Copy-Item -LiteralPath $artifact -Destination (Join-Path $stageDir $executableName) -Force
    Copy-Item -LiteralPath $licenseSource -Destination (Join-Path $stageDir $licenseName) -Force
    if ($null -ne $customCharacterResourcesSource) {
        $customCharacterTarget = Resolve-SafeRelativePath `
            -Root $stageDir `
            -Relative $customCharacterInstallRelative `
            -Label "custom character install directory"
        New-Item -ItemType Directory -Path $customCharacterTarget -Force | Out-Null
        foreach ($characterSource in Get-ChildItem -LiteralPath $customCharacterResourcesSource -Directory) {
            $characterManifestPath = Join-Path $characterSource.FullName "character.json"
            if (-not (Test-Path -LiteralPath $characterManifestPath -PathType Leaf)) {
                continue
            }
            $characterManifest = Get-Content -Raw -LiteralPath $characterManifestPath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
            $characterId = Get-RequiredString -Object $characterManifest -Name "id" -Context "custom character manifest"
            if ($characterId -notmatch "^[A-Za-z0-9_-]+$") {
                throw "Custom character id contains unsafe characters: '$characterId'"
            }
            $characterTarget = Resolve-SafeRelativePath `
                -Root $customCharacterTarget `
                -Relative $characterId `
                -Label "custom character directory"
            New-Item -ItemType Directory -Path $characterTarget -Force | Out-Null
            $declaredFiles = @("character.json")
            foreach ($propertyName in @(
                "asset",
                "source_template",
                "ai_source_atlas",
                "palette_config",
                "atlas_preview",
                "parts_contact_sheet",
                "spriter_project_archive",
                "official_export_archive",
                "official_release_probe"
            )) {
                $value = $characterManifest.$propertyName
                if ($value -is [string] -and -not [string]::IsNullOrWhiteSpace($value)) {
                    if ([IO.Path]::IsPathRooted($value) -or [IO.Path]::GetFileName($value) -ne $value) {
                        throw "Custom character $characterId.$propertyName must be a safe leaf filename."
                    }
                    $declaredFiles += $value
                }
            }
            foreach ($fileName in $declaredFiles | Select-Object -Unique) {
                $sourceFile = Resolve-SafeRelativePath `
                    -Root $characterSource.FullName `
                    -Relative $fileName `
                    -Label "custom character file"
                if (-not (Test-Path -LiteralPath $sourceFile -PathType Leaf)) {
                    throw "Declared custom character file does not exist: '$sourceFile'"
                }
                Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $characterTarget $fileName) -Force
            }
        }
    }
    [IO.File]::WriteAllText(
        (Join-Path $stageDir $originName),
        (($origin | ConvertTo-Json -Depth 10) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllText(
        (Join-Path $stageDir $versionName),
        ($version + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    $installedHash = (Get-FileHash -LiteralPath (Join-Path $stageDir $executableName) -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($installedHash -ne $artifactHash) {
        throw "Installed executable hash does not match the source artifact."
    }

    if (Test-Path -LiteralPath $targetDir) {
        Assert-PathInside -Path $targetDir -ParentPath $builtInToolsRoot -Label "existing character viewer install"
        Remove-Item -LiteralPath $targetDir -Recurse -Force
    }
    Move-Item -LiteralPath $stageDir -Destination $targetDir

    $manifestPath = Join-Path $skillRootPath "references\tooling-manifest.json"
    $toolEntry = [ordered]@{
        name = $toolName
        version = $version
        local_path = ($installRelative.TrimEnd("/", "\") + "/" + $executableName).Replace("\", "/")
        source = "AssetArchive-Dev derived local Tauri viewer"
        source_repository = $sourceRootPath
        source_remote = $remote
        source_commit = $commit.ToLowerInvariant()
        source_dirty = $dirty
        build_process_environment = $buildEnvironment
        custom_character_resources = $customCharacterResourcesRelative
        install_config = "references/game-character-animation-viewer-config.json"
        install_script = "scripts/install_game_character_animation_viewer_tool.ps1"
        license = "MIT; copied beside the installed executable"
        sha256 = $artifactHash
        purpose = "Character-first Don't Starve Together animation browsing and live playback from the installed game root; independent of the legacy GameAnimationViewer Bank manifest."
    }
    Update-ToolingManifest -ManifestPath $manifestPath -Entry $toolEntry
}
finally {
    if (Test-Path -LiteralPath $stageDir) {
        Assert-PathInside -Path $stageDir -ParentPath $builtInToolsRoot -Label "character viewer stage cleanup"
        Remove-Item -LiteralPath $stageDir -Recurse -Force
    }
}

$result = [ordered]@{
    status = "installed"
    tool = $toolName
    version = $version
    executable = (Join-Path $targetDir $executableName)
    sha256 = $artifactHash
    source_commit = $commit.ToLowerInvariant()
    source_dirty = $dirty
}
$result | ConvertTo-Json -Depth 5
