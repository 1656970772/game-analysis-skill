param(
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [string]$MergedIndexDir = "",
    [int]$PngSampleSize = 25,
    [int]$GltfSampleSize = 25,
    [switch]$VerifyAllPng,
    [switch]$VerifyAllGltf
)

$ErrorActionPreference = "Stop"

$OutputRoot = (Resolve-Path -LiteralPath $OutputRoot).Path
if (-not $MergedIndexDir) {
    $MergedIndexDir = Join-Path $OutputRoot "_merged_index"
}
New-Item -ItemType Directory -Force -Path $MergedIndexDir | Out-Null

function Convert-RelPath {
    param([string]$Rel)
    return ($Rel -replace "/", "\").TrimStart("\")
}

function Join-Relative {
    param(
        [string]$Base,
        [string]$Rel
    )
    return Join-Path $Base (Convert-RelPath $Rel)
}

function Test-PngSignature {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $expected = [byte[]](137, 80, 78, 71, 13, 10, 26, 10)
    $stream = [IO.File]::OpenRead($Path)
    try {
        if ($stream.Length -lt 8) { return $false }
        $actual = New-Object byte[] 8
        [void]$stream.Read($actual, 0, 8)
        for ($i = 0; $i -lt 8; $i++) {
            if ($actual[$i] -ne $expected[$i]) { return $false }
        }
        return $true
    }
    finally {
        $stream.Close()
    }
}

function Test-GltfBuffers {
    param([string]$Path)
    $errors = New-Object System.Collections.Generic.List[string]
    try {
        $json = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    catch {
        $errors.Add("json_parse_error")
        return $errors
    }

    if ($json.buffers) {
        foreach ($buffer in @($json.buffers)) {
            $uri = [string]$buffer.uri
            if (-not $uri) { continue }
            if ($uri.StartsWith("data:", [StringComparison]::OrdinalIgnoreCase)) { continue }
            $bufferPath = Join-Path (Split-Path -Parent $Path) (Convert-RelPath $uri)
            if (-not (Test-Path -LiteralPath $bufferPath)) {
                $errors.Add("missing_buffer:$uri")
            }
            elseif ($buffer.byteLength -and ((Get-Item -LiteralPath $bufferPath).Length -lt [int64]$buffer.byteLength)) {
                $errors.Add("short_buffer:$uri")
            }
        }
    }
    return $errors
}

$indexFiles = Get-ChildItem -LiteralPath $OutputRoot -Recurse -Filter "converted_assets_index.csv" -File |
    Where-Object { $_.FullName -notlike "*\_merged_index\*" } |
    Sort-Object FullName

if (-not $indexFiles) {
    throw "No converted_assets_index.csv files found under: $OutputRoot"
}

$mergedRows = New-Object System.Collections.Generic.List[object]
foreach ($indexFile in $indexFiles) {
    $indexDir = Split-Path -Parent $indexFile.FullName
    $sliceRoot = Split-Path -Parent $indexDir
    foreach ($row in (Import-Csv -LiteralPath $indexFile.FullName)) {
        $rawPath = Join-Relative (Join-Path $sliceRoot "raw_umodel") $row.raw_output_relative_path
        $typedPath = Join-Relative $sliceRoot $row.typed_output_relative_path
        $rawExists = Test-Path -LiteralPath $rawPath
        $rawSize = if ($rawExists) { (Get-Item -LiteralPath $rawPath).Length } else { $null }

        $mergedRows.Add([pscustomobject]@{
            slice_id = $row.slice_id
            slice_name = $row.slice_name
            asset_type = $row.asset_type
            extension = $row.extension
            bytes = [int64]$row.bytes
            raw_exists = $rawExists
            raw_size = $rawSize
            raw_size_matches_index = ($rawExists -and ([int64]$row.bytes -eq [int64]$rawSize))
            source_relative_guess = $row.source_relative_guess
            raw_output_relative_path = $row.raw_output_relative_path
            typed_output_relative_path = $row.typed_output_relative_path
            raw_absolute_path = $rawPath
            typed_absolute_path = $typedPath
            link_mode = $row.link_mode
            tool = $row.tool
            game_tag = $row.game_tag
            permission_scope = $row.permission_scope
            source_index = $indexFile.FullName
        })
    }
}

$mergedPath = Join-Path $MergedIndexDir "all_converted_assets_index.csv"
$mergedRows | Export-Csv -LiteralPath $mergedPath -NoTypeInformation -Encoding UTF8

$pngRows = @($mergedRows | Where-Object { $_.extension -eq ".png" })
$gltfRows = @($mergedRows | Where-Object { $_.extension -eq ".gltf" })

$pngToVerify = if ($VerifyAllPng) { $pngRows } else { $pngRows | Select-Object -First $PngSampleSize }
$gltfToVerify = if ($VerifyAllGltf) { $gltfRows } else { $gltfRows | Select-Object -First $GltfSampleSize }

$pngFailures = New-Object System.Collections.Generic.List[object]
foreach ($row in $pngToVerify) {
    if (-not (Test-PngSignature $row.raw_absolute_path)) {
        $pngFailures.Add([pscustomobject]@{
            slice_id = $row.slice_id
            raw_output_relative_path = $row.raw_output_relative_path
            raw_absolute_path = $row.raw_absolute_path
            issue = "bad_png_signature"
        })
    }
}

$gltfFailures = New-Object System.Collections.Generic.List[object]
foreach ($row in $gltfToVerify) {
    foreach ($issue in (Test-GltfBuffers $row.raw_absolute_path)) {
        $gltfFailures.Add([pscustomobject]@{
            slice_id = $row.slice_id
            raw_output_relative_path = $row.raw_output_relative_path
            raw_absolute_path = $row.raw_absolute_path
            issue = $issue
        })
    }
}

$missingRaw = @($mergedRows | Where-Object { -not $_.raw_exists })
$sizeMismatch = @($mergedRows | Where-Object { $_.raw_exists -and -not $_.raw_size_matches_index })
$zeroByte = @($mergedRows | Where-Object { $_.raw_exists -and ([int64]$_.raw_size -eq 0) })

$missingRaw | Export-Csv -LiteralPath (Join-Path $MergedIndexDir "missing_raw_files.csv") -NoTypeInformation -Encoding UTF8
$sizeMismatch | Export-Csv -LiteralPath (Join-Path $MergedIndexDir "size_mismatch_files.csv") -NoTypeInformation -Encoding UTF8
$zeroByte | Export-Csv -LiteralPath (Join-Path $MergedIndexDir "zero_byte_files.csv") -NoTypeInformation -Encoding UTF8
$pngFailures | Export-Csv -LiteralPath (Join-Path $MergedIndexDir "png_bad_signatures.csv") -NoTypeInformation -Encoding UTF8
$gltfFailures | Export-Csv -LiteralPath (Join-Path $MergedIndexDir "gltf_missing_buffers.csv") -NoTypeInformation -Encoding UTF8

$countsByType = $mergedRows | Group-Object asset_type | Sort-Object Name | ForEach-Object {
    [pscustomobject]@{ asset_type = $_.Name; count = $_.Count }
}
$countsByExtension = $mergedRows | Group-Object extension | Sort-Object Name | ForEach-Object {
    [pscustomobject]@{ extension = $_.Name; count = $_.Count }
}
$countsByType | Export-Csv -LiteralPath (Join-Path $MergedIndexDir "counts_by_type.csv") -NoTypeInformation -Encoding UTF8
$countsByExtension | Export-Csv -LiteralPath (Join-Path $MergedIndexDir "counts_by_extension.csv") -NoTypeInformation -Encoding UTF8

$summary = [pscustomobject]@{
    output_root = $OutputRoot
    merged_index_dir = $MergedIndexDir
    index_file_count = $indexFiles.Count
    total_indexed_outputs = $mergedRows.Count
    missing_raw_files = $missingRaw.Count
    size_mismatch_files = $sizeMismatch.Count
    zero_byte_files = $zeroByte.Count
    png_checked = @($pngToVerify).Count
    png_bad_signatures = $pngFailures.Count
    gltf_checked = @($gltfToVerify).Count
    gltf_missing_buffers = $gltfFailures.Count
    generated_at = (Get-Date).ToString("o")
    merged_index = $mergedPath
}

$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $MergedIndexDir "verification_summary.json") -Encoding UTF8
Write-Host "MERGED_INDEX=$mergedPath"
Write-Host "TOTAL_INDEXED_OUTPUTS=$($mergedRows.Count)"
Write-Host "MISSING_RAW_FILES=$($missingRaw.Count)"
Write-Host "SIZE_MISMATCH_FILES=$($sizeMismatch.Count)"
Write-Host "ZERO_BYTE_FILES=$($zeroByte.Count)"
Write-Host "PNG_BAD_SIGNATURES=$($pngFailures.Count)"
Write-Host "GLTF_MISSING_BUFFERS=$($gltfFailures.Count)"
