param(
  [string]$SourceBundleName = "VIVY_voice_bundle",
  [string]$SlimBundleName = "VIVY_voice_bundle_slim"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-RobocopyCopy {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )

  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
  & robocopy $Source $Destination /E /NFL /NDL /NJH /NJS /NC /NS /NP /R:1 /W:1 | Out-Null
  if ($LASTEXITCODE -gt 7) {
    throw "robocopy failed for $Source -> $Destination with exit code $LASTEXITCODE"
  }
}

function Remove-Target {
  param(
    [Parameter(Mandatory = $true)][string]$Path
  )

  if (Test-Path -LiteralPath $Path) {
    Remove-Item -LiteralPath $Path -Recurse -Force
  }
}

function Get-TreeSizeBytes {
  param(
    [Parameter(Mandatory = $true)][string]$Path
  )

  if (!(Test-Path -LiteralPath $Path)) {
    return 0
  }
  $item = Get-Item -LiteralPath $Path
  if (-not $item.PSIsContainer) {
    return [int64]$item.Length
  }
  $sum = (Get-ChildItem -LiteralPath $Path -Recurse -File | Measure-Object Length -Sum).Sum
  if ($null -eq $sum) {
    return 0
  }
  return [int64]$sum
}

$vivyRoot = Split-Path -Parent $PSScriptRoot
$distRoot = Join-Path $vivyRoot "dist"
$sourceBundle = Join-Path $distRoot $SourceBundleName
$sourceZip = Join-Path $distRoot ($SourceBundleName + ".zip")
$slimBundle = Join-Path $distRoot $SlimBundleName
$slimZip = Join-Path $distRoot ($SlimBundleName + ".zip")

if (!(Test-Path -LiteralPath $sourceBundle)) {
  throw "Source bundle not found: $sourceBundle"
}

$sourceSize = Get-TreeSizeBytes -Path $sourceBundle

if (Test-Path -LiteralPath $slimBundle) {
  Remove-Item -LiteralPath $slimBundle -Recurse -Force
}
if (Test-Path -LiteralPath $slimZip) {
  Remove-Item -LiteralPath $slimZip -Force
}

Invoke-RobocopyCopy -Source $sourceBundle -Destination $slimBundle

$removeTargets = @(
  (Join-Path $slimBundle "GPT-SoVITS\tools\asr"),
  (Join-Path $slimBundle "GPT-SoVITS\tools\uvr5"),
  (Join-Path $slimBundle "GPT-SoVITS\tools\AP_BWE_main"),
  (Join-Path $slimBundle "GPT-SoVITS\GPT_SoVITS\pretrained_models\gsv-v4-pretrained"),
  (Join-Path $slimBundle "GPT-SoVITS\GPT_SoVITS\pretrained_models\models--nvidia--bigvgan_v2_24khz_100band_256x"),
  (Join-Path $slimBundle "GPT-SoVITS\GPT_SoVITS\pretrained_models\v2Pro"),
  (Join-Path $slimBundle "GPT-SoVITS\GPT_SoVITS\pretrained_models\s1v3.ckpt"),
  (Join-Path $slimBundle "GPT-SoVITS\GPT_SoVITS\pretrained_models\s2Gv3.pth"),
  (Join-Path $slimBundle "GPT-SoVITS\GPT_SoVITS\pretrained_models\s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt"),
  (Join-Path $slimBundle "GPT-SoVITS\GPT_SoVITS\pretrained_models\s2G488k.pth"),
  (Join-Path $slimBundle "GPT-SoVITS\GPT_SoVITS\pretrained_models\s2D488k.pth")
)

foreach ($target in $removeTargets) {
  Remove-Target -Path $target
}

$notePath = Join-Path $slimBundle "SLIM_NOTES.txt"
$note = @'
VIVY Safe Slim Bundle
=====================

This slim bundle keeps the current desktop pet workflow and current GPT-SoVITS voice setup.

Kept:
- VIVY desktop pet program
- Current violate voice weights
- Current reference audio
- Current GPT-SoVITS runtime needed by local /tts service
- Current Chinese text frontend models and v2 base resources

Removed to reduce package size:
- tools\asr
- tools\uvr5
- tools\AP_BWE_main
- Unused v1 / v3 / v4 / v2Pro pretrained model files not needed by the current packaged voice setup

What this slim bundle is intended for:
- Running the packaged VIVY desktop pet with the bundled local voice service

What is no longer guaranteed:
- Reusing this package as a general-purpose GPT-SoVITS training / preprocessing / separation toolkit
- Switching to removed GPT-SoVITS model families without restoring those pretrained files
'@
Set-Content -LiteralPath $notePath -Value $note -Encoding UTF8

$slimSize = Get-TreeSizeBytes -Path $slimBundle
Compress-Archive -Path (Join-Path $slimBundle "*") -DestinationPath $slimZip -Force

Write-Host "Full bundle kept at: $sourceBundle"
Write-Host "Full zip kept at: $sourceZip"
Write-Host "Slim bundle ready: $slimBundle"
Write-Host "Slim zip ready: $slimZip"
Write-Host ("Source size GB: {0:N2}" -f ($sourceSize / 1GB))
Write-Host ("Slim size GB: {0:N2}" -f ($slimSize / 1GB))
