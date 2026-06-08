param(
  [Parameter(Mandatory = $true)]
  [string]$GameRoot,

  [Parameter(Mandatory = $true)]
  [string]$StudyRoot,

  [string]$SkillRoot = "",

  [switch]$SkipSounds,

  [switch]$SkipStrings
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($SkillRoot)) {
  if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    throw "SkillRoot was not provided and PSScriptRoot is empty. Pass -SkillRoot explicitly."
  }
  $SkillRoot = Split-Path -Parent $PSScriptRoot
}

function Assert-AsciiPath([string]$PathValue, [string]$Name) {
  if ($PathValue.ToCharArray() | Where-Object { [int][char]$_ -gt 127 } | Select-Object -First 1) {
    throw "$Name must be ASCII-only for UndertaleModCli scripted prompts. Use an ASCII study path such as F:\MyTools\KatanaZERO_ArtStudy."
  }
}

function Write-AsciiInput([string]$Path, [string[]]$Lines) {
  [System.IO.File]::WriteAllText($Path, (($Lines -join "`r`n") + "`r`n"), [System.Text.Encoding]::ASCII)
}

function Invoke-UtmtScript([string]$ScriptPath, [string[]]$PromptLines, [string]$LogPath) {
  $stdinPath = [System.IO.Path]::ChangeExtension($LogPath, ".stdin.txt")
  Write-AsciiInput -Path $stdinPath -Lines $PromptLines
  $cmd = '"' + $script:CliExe + '" load "' + $script:DataWin + '" --scripts "' + $ScriptPath + '" --verbose < "' + $stdinPath + '"'
  cmd.exe /d /c $cmd 2>&1 | Tee-Object -FilePath $LogPath
}

$resolvedGameRoot = (Resolve-Path -LiteralPath $GameRoot).Path
$resolvedStudyRoot = (Resolve-Path -LiteralPath (New-Item -ItemType Directory -Force -Path $StudyRoot)).Path
Assert-AsciiPath -PathValue $resolvedStudyRoot -Name "StudyRoot"

$script:DataWin = Join-Path $resolvedGameRoot "data.win"
if (-not (Test-Path -LiteralPath $script:DataWin)) {
  throw "Cannot find data.win under '$resolvedGameRoot'."
}

$script:CliExe = Join-Path $SkillRoot "tools\UTMT_CLI_v0.9.0.0-Windows\UndertaleModCli.exe"
$utmtScripts = Join-Path $SkillRoot "tools\UndertaleModTool_v0.9.0.0-Windows\Scripts\Resource Exporters"
if (-not (Test-Path -LiteralPath $script:CliExe)) {
  throw "Cannot find UndertaleModCli.exe at '$script:CliExe'."
}
if (-not (Test-Path -LiteralPath $utmtScripts)) {
  throw "Cannot find UndertaleModTool exporter scripts at '$utmtScripts'."
}

$exportRoot = Join-Path $resolvedStudyRoot "GameMaker_UTMT_Exports"
New-Item -ItemType Directory -Force -Path $exportRoot | Out-Null

& $script:CliExe info $script:DataWin --verbose 2>&1 |
  Tee-Object -FilePath (Join-Path $resolvedStudyRoot "gamemaker_datawin_info.txt")

$embeddedTextures = Join-Path $exportRoot "EmbeddedTextures"
$sprites = Join-Path $exportRoot "Sprites"
$textureItems = Join-Path $exportRoot "TextureItems"
New-Item -ItemType Directory -Force -Path $embeddedTextures, $sprites, $textureItems | Out-Null

Invoke-UtmtScript `
  -ScriptPath (Join-Path $utmtScripts "ExportAllEmbeddedTextures.csx") `
  -PromptLines @($embeddedTextures) `
  -LogPath (Join-Path $exportRoot "export_embedded_textures.log")

Invoke-UtmtScript `
  -ScriptPath (Join-Path $utmtScripts "ExportAllSprites.csx") `
  -PromptLines @($sprites, "y", "y") `
  -LogPath (Join-Path $exportRoot "export_sprites.log")

Invoke-UtmtScript `
  -ScriptPath (Join-Path $utmtScripts "ExportAllTextures.csx") `
  -PromptLines @($textureItems) `
  -LogPath (Join-Path $exportRoot "export_textures.log")

if (-not $SkipSounds) {
  $sounds = Join-Path $exportRoot "Sounds"
  New-Item -ItemType Directory -Force -Path $sounds | Out-Null
  Invoke-UtmtScript `
    -ScriptPath (Join-Path $utmtScripts "ExportAllSounds.csx") `
    -PromptLines @($sounds, "y", "y") `
    -LogPath (Join-Path $exportRoot "export_sounds.log")
}

if (-not $SkipStrings) {
  Invoke-UtmtScript `
    -ScriptPath (Join-Path $utmtScripts "ExportAllStringsJSON.csx") `
    -PromptLines @((Join-Path $exportRoot "strings.json")) `
    -LogPath (Join-Path $exportRoot "export_strings_json.log")
}

Write-Output (ConvertTo-Json @{
  gameRoot = $resolvedGameRoot
  studyRoot = $resolvedStudyRoot
  exportRoot = $exportRoot
  dataWin = $script:DataWin
} -Depth 3)
