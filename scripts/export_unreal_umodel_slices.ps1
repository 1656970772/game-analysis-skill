param(
    [Parameter(Mandatory = $true)]
    [string]$StudyRoot,

    [Parameter(Mandatory = $true)]
    [string]$SliceManifest,

    [string]$PakUnpackedRoot = "",
    [string]$ContentRoot = "",
    [string]$OutputRoot = "",
    [string]$WorkRoot = "",
    [string]$UModelPath = "",
    [string]$GameTag = "ue4.26",
    [switch]$SkipRecovery
)

$ErrorActionPreference = "Stop"

if (-not $UModelPath) {
    $SkillRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    $UModelPath = Join-Path $SkillRoot "tools\UEViewer_umodel\umodel_64.exe"
}
if (-not (Test-Path -LiteralPath $UModelPath)) {
    throw "UModel not found: $UModelPath"
}

$StudyRoot = (Resolve-Path -LiteralPath $StudyRoot).Path
if (-not $PakUnpackedRoot) {
    $PakUnpackedRoot = Get-ChildItem -LiteralPath $StudyRoot -Directory -Recurse -Depth 3 |
        Where-Object { $_.Name -eq "PakUnpacked" } |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $PakUnpackedRoot) {
    throw "Could not locate PakUnpacked. Pass -PakUnpackedRoot explicitly."
}
$PakUnpackedRoot = (Resolve-Path -LiteralPath $PakUnpackedRoot).Path

if (-not $ContentRoot) {
    if (Test-Path -LiteralPath (Join-Path $PakUnpackedRoot "Content")) {
        $ContentRoot = Join-Path $PakUnpackedRoot "Content"
    }
    else {
        $ContentRoot = Get-ChildItem -LiteralPath $PakUnpackedRoot -Directory |
            Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "Content") } |
            Select-Object -First 1 |
            ForEach-Object { Join-Path $_.FullName "Content" }
    }
}
if (-not $ContentRoot) {
    throw "Could not locate Unreal Content root. Pass -ContentRoot explicitly."
}
$ContentRoot = (Resolve-Path -LiteralPath $ContentRoot).Path

if (-not $OutputRoot) {
    $exportRoot = Split-Path -Parent $PakUnpackedRoot
    $OutputRoot = Join-Path $exportRoot "FullConverted"
}
if (-not $WorkRoot) {
    $WorkRoot = Join-Path $StudyRoot "4.临时目录\_ascii_work\umodel-slices"
}

New-Item -ItemType Directory -Force -Path $OutputRoot, $WorkRoot | Out-Null

function Convert-RelPath {
    param([string]$Rel)
    return ($Rel -replace "/", "\").TrimStart("\")
}

function Resolve-IncludeTarget {
    param($Include)
    $root = if ($Include.root) { [string]$Include.root } else { "Content" }
    $rel = Convert-RelPath ([string]$Include.rel)
    switch ($root) {
        "Content" { return Join-Path $ContentRoot $rel }
        "PakRoot" { return Join-Path $PakUnpackedRoot $rel }
        "Absolute" { return $rel }
        default { throw "Unknown include root '$root'. Use Content, PakRoot, or Absolute." }
    }
}

function Ensure-Junction {
    param(
        [string]$LinkPath,
        [string]$TargetPath
    )
    if (-not (Test-Path -LiteralPath $TargetPath)) {
        return [pscustomobject]@{ link = $LinkPath; target = $TargetPath; status = "missing_source" }
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LinkPath) | Out-Null
    if (Test-Path -LiteralPath $LinkPath) {
        $item = Get-Item -LiteralPath $LinkPath -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
            throw "Staging path exists and is not a junction: $LinkPath"
        }
        return [pscustomobject]@{ link = $LinkPath; target = $TargetPath; status = "exists" }
    }
    New-Item -ItemType Junction -Path $LinkPath -Target $TargetPath | Out-Null
    return [pscustomobject]@{ link = $LinkPath; target = $TargetPath; status = "created" }
}

function Invoke-UModel {
    param(
        [string]$Mode,
        [string[]]$ExtraArgs,
        [string]$Package,
        [string]$InputRoot,
        [string]$RawOut,
        [string]$LogDir
    )
    if (-not $Package) { $Package = "*" }
    $log = Join-Path $LogDir ("umodel_{0}.log" -f ($Mode -replace "[^A-Za-z0-9_.-]", "_"))
    $args = @("-export", "-game=$GameTag", "-png", "-gltf", "-nooverwrite") +
        $ExtraArgs + @("-out=$RawOut", "-path=$InputRoot", $Package)

    $started = Get-Date
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $UModelPath @args *> $log
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $old
    $ended = Get-Date

    return [pscustomobject]@{
        mode = $Mode
        package = $Package
        exit_code = $exit
        log = $log
        started_at = $started.ToString("o")
        ended_at = $ended.ToString("o")
        command = "$UModelPath $($args -join ' ')"
    }
}

function Get-AssetType {
    param([string]$Extension)
    switch ($Extension.ToLowerInvariant()) {
        ".png" { "texture_png"; break }
        ".tga" { "texture_tga"; break }
        ".dds" { "texture_dds"; break }
        ".hdr" { "texture_hdr"; break }
        ".gltf" { "model_gltf"; break }
        ".glb" { "model_glb"; break }
        ".bin" { "model_gltf_buffer"; break }
        ".psk" { "model_psk"; break }
        ".pskx" { "model_pskx"; break }
        ".psa" { "animation_psa"; break }
        ".md5mesh" { "model_md5"; break }
        ".md5anim" { "animation_md5"; break }
        ".mat" { "material_metadata"; break }
        ".txt" { "metadata_text"; break }
        default { "other_converted"; break }
    }
}

function Get-TypeFolder {
    param([string]$AssetType)
    switch ($AssetType) {
        "texture_png" { "Textures_PNG"; break }
        "texture_tga" { "Textures_TGA"; break }
        "texture_dds" { "Textures_DDS"; break }
        "texture_hdr" { "Textures_HDR"; break }
        "model_gltf" { "Models_GLTF"; break }
        "model_glb" { "Models_GLTF"; break }
        "model_gltf_buffer" { "Models_GLTF"; break }
        "model_psk" { "Models_PSK_PSKX"; break }
        "model_pskx" { "Models_PSK_PSKX"; break }
        "model_md5" { "Models_MD5"; break }
        "animation_psa" { "Animations"; break }
        "animation_md5" { "Animations"; break }
        "material_metadata" { "Materials_Metadata"; break }
        "metadata_text" { "Materials_Metadata"; break }
        default { "Other_Converted"; break }
    }
}

function New-HardLinkOrCopy {
    param(
        [string]$Target,
        [string]$Path
    )
    if (Test-Path -LiteralPath $Path) { return "exists" }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    try {
        New-Item -ItemType HardLink -Path $Path -Target $Target | Out-Null
        return "hardlink"
    }
    catch {
        Copy-Item -LiteralPath $Target -Destination $Path
        return "copy"
    }
}

$manifest = Get-Content -LiteralPath $SliceManifest -Raw | ConvertFrom-Json
if ($manifest.slices) { $slices = @($manifest.slices) } else { $slices = @($manifest) }

$sliceSummaries = New-Object System.Collections.Generic.List[object]

foreach ($slice in $slices) {
    $sliceId = [string]$slice.slice_id
    if (-not $sliceId) { throw "Each slice needs slice_id." }

    $inputRoot = Join-Path (Join-Path $WorkRoot "inputs") $sliceId
    $sliceOut = Join-Path $OutputRoot $sliceId
    $rawOut = Join-Path $sliceOut "raw_umodel"
    $typeOut = Join-Path $sliceOut "by_type"
    $logDir = Join-Path $sliceOut "logs"
    $indexDir = Join-Path $sliceOut "index"
    New-Item -ItemType Directory -Force -Path $inputRoot, $rawOut, $typeOut, $logDir, $indexDir | Out-Null

    $startedAll = Get-Date
    $staging = foreach ($inc in @($slice.includes)) {
        $target = Resolve-IncludeTarget $inc
        $destRel = if ($inc.dest) { [string]$inc.dest } else { [string]$inc.rel }
        $link = Join-Path $inputRoot (Convert-RelPath $destRel)
        Ensure-Junction -LinkPath $link -TargetPath $target
    }
    $staging | Export-Csv -LiteralPath (Join-Path $indexDir "staging_junctions.csv") -NoTypeInformation -Encoding UTF8

    $runs = New-Object System.Collections.Generic.List[object]
    $runs.Add((Invoke-UModel -Mode "01_textures_materials" -ExtraArgs @("-nomesh", "-nostat", "-noanim", "-novert") -InputRoot $inputRoot -RawOut $rawOut -LogDir $logDir))
    $runs.Add((Invoke-UModel -Mode "02_full_models" -ExtraArgs @() -InputRoot $inputRoot -RawOut $rawOut -LogDir $logDir))

    $recoveryFailures = New-Object System.Collections.Generic.List[object]
    $lastRun = $runs[$runs.Count - 1]
    if (($lastRun.exit_code -ne 0) -and (-not $SkipRecovery)) {
        $candidateFile = Join-Path $indexDir "model_recovery_candidates.txt"
        $rg = Get-Command rg -ErrorAction SilentlyContinue
        if ($rg) {
            $old = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            & $rg.Source -a -l --glob "*.uasset" "StaticMesh|SkeletalMesh|AnimSequence|AnimMontage|Skeleton" $inputRoot *> $candidateFile
            $ErrorActionPreference = $old
        }
        else {
            Get-ChildItem -LiteralPath $inputRoot -Recurse -Filter "*.uasset" |
                Select-Object -ExpandProperty FullName |
                Set-Content -LiteralPath $candidateFile -Encoding UTF8
        }

        $i = 0
        foreach ($file in (Get-Content -LiteralPath $candidateFile -ErrorAction SilentlyContinue)) {
            if (-not $file) { continue }
            $rel = $file.Substring($inputRoot.Length).TrimStart("\") -replace "\\", "/"
            $pkg = [IO.Path]::ChangeExtension($rel, $null)
            $run = Invoke-UModel -Mode ("03_recover_{0:D6}" -f $i) -ExtraArgs @() -Package $pkg -InputRoot $inputRoot -RawOut $rawOut -LogDir $logDir
            $runs.Add($run)
            if ($run.exit_code -ne 0) {
                $recoveryFailures.Add([pscustomobject]@{ package = $pkg; exit_code = $run.exit_code; log = $run.log })
            }
            $i++
        }
    }

    $files = Get-ChildItem -LiteralPath $rawOut -Recurse -File -ErrorAction SilentlyContinue
    $indexRows = New-Object System.Collections.Generic.List[object]
    foreach ($file in $files) {
        $rawRel = $file.FullName.Substring($rawOut.Length).TrimStart("\")
        $assetType = Get-AssetType $file.Extension
        $typePath = Join-Path (Join-Path $typeOut (Get-TypeFolder $assetType)) $rawRel
        $linkMode = New-HardLinkOrCopy -Target $file.FullName -Path $typePath
        $indexRows.Add([pscustomobject]@{
            slice_id = $sliceId
            slice_name = [string]$slice.name
            asset_type = $assetType
            extension = $file.Extension.ToLowerInvariant()
            bytes = $file.Length
            source_relative_guess = ([IO.Path]::ChangeExtension($rawRel, ".uasset") -replace "\\", "/")
            raw_output_relative_path = ($rawRel -replace "\\", "/")
            typed_output_relative_path = ($typePath.Substring($sliceOut.Length).TrimStart("\") -replace "\\", "/")
            link_mode = $linkMode
            tool = "UE Viewer UModel"
            game_tag = $GameTag
            permission_scope = "local_study_only"
        })
    }

    $indexPath = Join-Path $indexDir "converted_assets_index.csv"
    $indexRows | Export-Csv -LiteralPath $indexPath -NoTypeInformation -Encoding UTF8
    $runs | Export-Csv -LiteralPath (Join-Path $indexDir "umodel_runs.csv") -NoTypeInformation -Encoding UTF8
    $recoveryFailures | Export-Csv -LiteralPath (Join-Path $indexDir "model_recovery_failures.csv") -NoTypeInformation -Encoding UTF8

    $summary = [pscustomobject]@{
        slice_id = $sliceId
        slice_name = [string]$slice.name
        started_at = $startedAll.ToString("o")
        ended_at = (Get-Date).ToString("o")
        study_root = $StudyRoot
        pak_unpacked_root = $PakUnpackedRoot
        content_root = $ContentRoot
        input_root = $inputRoot
        output_root = $sliceOut
        raw_output = $rawOut
        typed_output = $typeOut
        index = $indexPath
        game_tag = $GameTag
        include_count = @($slice.includes).Count
        staged_count = ($staging | Where-Object { $_.status -ne "missing_source" } | Measure-Object).Count
        missing_source_count = ($staging | Where-Object { $_.status -eq "missing_source" } | Measure-Object).Count
        exported_file_count = $files.Count
        counts_by_extension = $files | Group-Object Extension | ForEach-Object { [pscustomobject]@{ extension = $_.Name; count = $_.Count } }
        counts_by_type = $indexRows | Group-Object asset_type | ForEach-Object { [pscustomobject]@{ asset_type = $_.Name; count = $_.Count } }
        run_count = $runs.Count
        nonzero_run_count = ($runs | Where-Object { $_.exit_code -ne 0 } | Measure-Object).Count
        recovery_failure_count = $recoveryFailures.Count
    }

    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $indexDir "summary.json") -Encoding UTF8
    $sliceSummaries.Add($summary)
    Write-Host "SLICE=$sliceId EXPORTED_FILE_COUNT=$($files.Count) NONZERO_RUN_COUNT=$($summary.nonzero_run_count)"
}

$sliceSummaries | Select-Object slice_id, slice_name, exported_file_count, nonzero_run_count, recovery_failure_count, missing_source_count, output_root, index |
    Export-Csv -LiteralPath (Join-Path $OutputRoot "slice_summary.csv") -NoTypeInformation -Encoding UTF8

Write-Host "OUTPUT_ROOT=$OutputRoot"
