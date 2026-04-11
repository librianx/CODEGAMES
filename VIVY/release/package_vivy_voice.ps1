param(
  [string]$BundleName = "VIVY_voice_bundle"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-RobocopyCopy {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination,
    [string[]]$ExtraArgs = @()
  )

  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
  $args = @($Source, $Destination, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS", "/NP", "/R:1", "/W:1") + $ExtraArgs
  & robocopy @args | Out-Null
  if ($LASTEXITCODE -gt 7) {
    throw "robocopy failed for $Source -> $Destination with exit code $LASTEXITCODE"
  }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$vivyRoot = $repoRoot
$pythonExe = Join-Path $vivyRoot ".venv\Scripts\python.exe"
$releaseRoot = Join-Path $vivyRoot "release"
$iconPng = Join-Path $vivyRoot "static\images\VIVYstatr.png"
$iconIco = Join-Path $releaseRoot "vivy.ico"
$buildRoot = Join-Path $vivyRoot "build\bundle_build"
$distRoot = Join-Path $vivyRoot "dist\bundle_build"
$bundleRoot = Join-Path $vivyRoot ("dist\" + $BundleName)
$bundleZip = Join-Path $vivyRoot ("dist\" + $BundleName + ".zip")

$gsvSourceRoot = "D:\Gpt-sovits\GPT-SoVITS-v2pro-20250604(1)\GPT-SoVITS-v2pro-20250604"
$gsvRefSource = Get-ChildItem -LiteralPath (Join-Path $gsvSourceRoot "output\slicer_opt") -File |
  Where-Object { $_.Name -like "*0000141440_0000303360.wav" } |
  Select-Object -ExpandProperty FullName -First 1
$gsvGptWeight = Join-Path $gsvSourceRoot "GPT_weights_v2\violate-e15.ckpt"
$gsvSovitsWeight = Join-Path $gsvSourceRoot "SoVITS_weights_v2\violate_e8_s136.pth"

$requiredPaths = @(
  $pythonExe,
  $iconPng,
  $gsvSourceRoot,
  $gsvRefSource,
  $gsvGptWeight,
  $gsvSovitsWeight
)
foreach ($path in $requiredPaths) {
  if (!(Test-Path -LiteralPath $path)) {
    throw "Missing required path: $path"
  }
}

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null

& $pythonExe -c "from PIL import Image; img=Image.open(r'$iconPng').convert('RGBA'); img.save(r'$iconIco', format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if ($LASTEXITCODE -ne 0) {
  throw "Icon generation failed."
}

if (Test-Path -LiteralPath $buildRoot) { Remove-Item -LiteralPath $buildRoot -Recurse -Force }
if (Test-Path -LiteralPath $distRoot) { Remove-Item -LiteralPath $distRoot -Recurse -Force }
if (Test-Path -LiteralPath $bundleRoot) { Remove-Item -LiteralPath $bundleRoot -Recurse -Force }
if (Test-Path -LiteralPath $bundleZip) { Remove-Item -LiteralPath $bundleZip -Force }

Push-Location $vivyRoot
try {
  & $pythonExe -m PyInstaller --noconfirm --windowed --name VIVY --icon $iconIco --add-data "static\images;static\images" --add-data "song;song" desktop_pet.py --distpath $distRoot --workpath $buildRoot
  if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
  }
}
finally {
  Pop-Location
}

$builtVivyDir = Join-Path $distRoot "VIVY"
if (!(Test-Path -LiteralPath $builtVivyDir)) {
  throw "Built VIVY directory not found: $builtVivyDir"
}

$bundleVivyDir = Join-Path $bundleRoot "VIVY"
$bundleGsvDir = Join-Path $bundleRoot "GPT-SoVITS"
Invoke-RobocopyCopy -Source $builtVivyDir -Destination $bundleVivyDir

$releaseEnvPath = Join-Path $bundleVivyDir ".env"
$releaseEnvContent = @'
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
FLASK_PORT=5000
LLM_SUMMARY=true
VIVY_IDLE_TIMEOUT=30
VIVY_TTS_MODE=gptsovits
VIVY_GSV_TTS_URL=http://127.0.0.1:9880/tts
VIVY_GSV_TEXT_LANG=zh
VIVY_GSV_PROMPT_LANG=zh
VIVY_GSV_SPEED_FACTOR=0.9
VIVY_GSV_TIMEOUT=120
VIVY_GSV_TRUE_STREAMING=true
VIVY_GSV_STREAMING_MODE=3
'@
Set-Content -LiteralPath $releaseEnvPath -Value $releaseEnvContent -Encoding UTF8

$internalDir = Join-Path $bundleVivyDir "_internal"
if (Test-Path -LiteralPath $internalDir) {
  Set-Content -LiteralPath (Join-Path $internalDir ".env") -Value $releaseEnvContent -Encoding UTF8
}

Invoke-RobocopyCopy -Source (Join-Path $gsvSourceRoot "runtime") -Destination (Join-Path $bundleGsvDir "runtime") -ExtraArgs @("/XD", "__pycache__")
Invoke-RobocopyCopy -Source (Join-Path $gsvSourceRoot "tools") -Destination (Join-Path $bundleGsvDir "tools") -ExtraArgs @("/XD", "__pycache__")
Invoke-RobocopyCopy -Source (Join-Path $gsvSourceRoot "GPT_SoVITS") -Destination (Join-Path $bundleGsvDir "GPT_SoVITS") -ExtraArgs @("/XD", "__pycache__")

Copy-Item -LiteralPath (Join-Path $gsvSourceRoot "api_v2.py") -Destination (Join-Path $bundleGsvDir "api_v2.py") -Force
Copy-Item -LiteralPath (Join-Path $gsvSourceRoot "api.py") -Destination (Join-Path $bundleGsvDir "api.py") -Force
Copy-Item -LiteralPath (Join-Path $gsvSourceRoot "config.py") -Destination (Join-Path $bundleGsvDir "config.py") -Force
Copy-Item -LiteralPath (Join-Path $gsvSourceRoot "weight.json") -Destination (Join-Path $bundleGsvDir "weight.json") -Force
Copy-Item -LiteralPath (Join-Path $gsvSourceRoot "requirements.txt") -Destination (Join-Path $bundleGsvDir "requirements.txt") -Force

New-Item -ItemType Directory -Force -Path (Join-Path $bundleGsvDir "GPT_weights_v2") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $bundleGsvDir "SoVITS_weights_v2") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $bundleGsvDir "ref_audio") | Out-Null

Copy-Item -LiteralPath $gsvGptWeight -Destination (Join-Path $bundleGsvDir "GPT_weights_v2\violate-e15.ckpt") -Force
Copy-Item -LiteralPath $gsvSovitsWeight -Destination (Join-Path $bundleGsvDir "SoVITS_weights_v2\violate_e8_s136.pth") -Force
$bundleRefAudio = Join-Path $bundleGsvDir "ref_audio\vivy_ref.wav"
Copy-Item -LiteralPath $gsvRefSource -Destination $bundleRefAudio -Force

$ttsConfigCpuPath = Join-Path $bundleGsvDir "GPT_SoVITS\configs\tts_infer_violate_cpu.yaml"
$ttsConfigCpu = @'
custom:
  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cpu
  is_half: false
  t2s_weights_path: GPT_weights_v2/violate-e15.ckpt
  version: v2
  vits_weights_path: SoVITS_weights_v2/violate_e8_s136.pth
'@
Set-Content -LiteralPath $ttsConfigCpuPath -Value $ttsConfigCpu -Encoding UTF8

$ttsConfigGpuPath = Join-Path $bundleGsvDir "GPT_SoVITS\configs\tts_infer_violate_gpu.yaml"
$ttsConfigGpu = @'
custom:
  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cuda
  is_half: true
  t2s_weights_path: GPT_weights_v2/violate-e15.ckpt
  version: v2
  vits_weights_path: SoVITS_weights_v2/violate_e8_s136.pth
'@
Set-Content -LiteralPath $ttsConfigGpuPath -Value $ttsConfigGpu -Encoding UTF8

$startPs1Path = Join-Path $bundleRoot "start_all.ps1"
$startPs1 = @'
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$bundleRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$gsvRoot = Join-Path $bundleRoot "GPT-SoVITS"
$vivyRoot = Join-Path $bundleRoot "VIVY"
$refAudio = Join-Path $gsvRoot "ref_audio\vivy_ref.wav"
$ttsConfigCpu = Join-Path $gsvRoot "GPT_SoVITS\configs\tts_infer_violate_cpu.yaml"
$ttsConfigGpu = Join-Path $gsvRoot "GPT_SoVITS\configs\tts_infer_violate_gpu.yaml"
$promptText = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("5L2T5Yqb5aSn5oq15oGi5aSN77yM5Y2z5L6/5pyJ5Lqb5Yqo5L2c5LiN5Y+Y44CC"))

function Test-GsvCudaAvailable {
  param(
    [Parameter(Mandatory = $true)][string]$PythonExe
  )

  try {
    $result = & $PythonExe -c "import torch; print('cuda' if torch.cuda.is_available() else 'cpu')" 2>$null
    return (($result | Select-Object -Last 1).Trim() -eq "cuda")
  }
  catch {
    return $false
  }
}

function Start-GsvServer {
  param(
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory,
    [Parameter(Mandatory = $true)][string]$ConfigPath
  )

  return Start-Process -FilePath $PythonExe -ArgumentList @("api_v2.py", "-a", "127.0.0.1", "-p", "9880", "-c", $ConfigPath) -WorkingDirectory $WorkingDirectory -PassThru
}

if (!(Test-Path -LiteralPath (Join-Path $gsvRoot "runtime\python.exe"))) { throw "Missing GPT-SoVITS runtime\\python.exe" }
if (!(Test-Path -LiteralPath (Join-Path $vivyRoot "VIVY.exe"))) { throw "Missing VIVY\\VIVY.exe" }

$env:VIVY_TTS_MODE = "gptsovits"
$env:VIVY_GSV_TTS_URL = "http://127.0.0.1:9880/tts"
$env:VIVY_GSV_REF_AUDIO = $refAudio
$env:VIVY_GSV_PROMPT_TEXT = $promptText
$env:VIVY_GSV_PROMPT_LANG = "zh"
$env:VIVY_GSV_TEXT_LANG = "zh"
$env:VIVY_GSV_SPEED_FACTOR = "0.9"
$env:VIVY_GSV_TIMEOUT = "120"
$env:VIVY_GSV_TRUE_STREAMING = "true"
$env:VIVY_GSV_STREAMING_MODE = "3"

$pythonRuntime = Join-Path $gsvRoot "runtime\python.exe"
$preferGpu = (Test-Path -LiteralPath $ttsConfigGpu) -and (Test-GsvCudaAvailable -PythonExe $pythonRuntime)
$selectedConfig = if ($preferGpu) { $ttsConfigGpu } else { $ttsConfigCpu }
$selectedMode = if ($preferGpu) { "GPU" } else { "CPU" }

Write-Host "Starting GPT-SoVITS in $selectedMode mode..."
$gsvProcess = Start-GsvServer -PythonExe $pythonRuntime -WorkingDirectory $gsvRoot -ConfigPath $selectedConfig

$ready = $false
$retriedOnCpu = $false
for ($i = 0; $i -lt 120; $i++) {
  Start-Sleep -Milliseconds 1000
  try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect("127.0.0.1", 9880)
    $tcp.Close()
    $ready = $true
    break
  }
  catch {
  }

  if ($gsvProcess.HasExited) {
    if ($selectedMode -eq "GPU" -and -not $retriedOnCpu) {
      Write-Warning "GPU mode failed to start. Falling back to CPU mode..."
      $selectedConfig = $ttsConfigCpu
      $selectedMode = "CPU"
      $retriedOnCpu = $true
      $gsvProcess = Start-GsvServer -PythonExe $pythonRuntime -WorkingDirectory $gsvRoot -ConfigPath $selectedConfig
      continue
    }
    throw "GPT-SoVITS failed to start. Exit code: $($gsvProcess.ExitCode)"
  }
}

if (-not $ready) {
  throw "GPT-SoVITS did not become ready within 120 seconds."
}

Write-Host "GPT-SoVITS is ready in $selectedMode mode."
Start-Process -FilePath (Join-Path $vivyRoot "VIVY.exe") -WorkingDirectory $vivyRoot | Out-Null
'@
Set-Content -LiteralPath $startPs1Path -Value $startPs1 -Encoding UTF8

$startBatPath = Join-Path $bundleRoot "start_all.bat"
$startBat = @'
@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0start_all.ps1"
'@
Set-Content -LiteralPath $startBatPath -Value $startBat -Encoding ASCII

$readmePath = Join-Path $bundleRoot "README_DEPLOY.txt"
$readme = @'
VIVY Portable Bundle
====================

How to start:
1. Double-click start_all.bat
2. The script starts local GPT-SoVITS first
3. VIVY.exe launches after the TTS service is ready

First run:
- Chat still needs your own DeepSeek or OpenAI-compatible API key
- Without an API key, VIVY falls back to offline mode
- Voice output is already fixed to the bundled VIVY voice assets

Included assets:
- VIVY\            desktop pet program
- GPT-SoVITS\      local TTS runtime, models, reference audio
- start_all.bat    one-click launcher
- start_all.ps1    launcher script

Bundled voice setup:
- GPT weight: GPT_weights_v2\violate-e15.ckpt
- SoVITS weight: SoVITS_weights_v2\violate_e8_s136.pth
- Ref audio: GPT-SoVITS\ref_audio\vivy_ref.wav
- Inference mode: auto-detect GPU, fallback to CPU
'@
Set-Content -LiteralPath $readmePath -Value $readme -Encoding UTF8

$swapGuidePath = Join-Path $bundleRoot "MODEL_SWAP_GUIDE.txt"
$swapGuide = @'
How to replace the packaged GPT-SoVITS voice model
==================================================

Files to replace:
1. Put your GPT checkpoint into GPT-SoVITS\GPT_weights_v2\
2. Put your SoVITS checkpoint into GPT-SoVITS\SoVITS_weights_v2\
3. Put your reference audio into GPT-SoVITS\ref_audio\

Files to edit:
1. GPT-SoVITS\GPT_SoVITS\configs\tts_infer_violate_cpu.yaml
2. GPT-SoVITS\GPT_SoVITS\configs\tts_infer_violate_gpu.yaml
3. start_all.ps1

What to change in the yaml files:
- t2s_weights_path: change to your GPT checkpoint filename
- vits_weights_path: change to your SoVITS checkpoint filename

What to change in start_all.ps1:
- $refAudio: change to your new reference audio filename
- $promptText: change to the transcript of that reference audio

Important rules:
- The reference audio text must match the spoken content of the reference audio
- Keep CPU config as device: cpu and is_half: false
- Keep GPU config as device: cuda and is_half: true unless your model requires otherwise
'@
Set-Content -LiteralPath $swapGuidePath -Value $swapGuide -Encoding UTF8

Compress-Archive -Path (Join-Path $bundleRoot "*") -DestinationPath $bundleZip -Force

Write-Host "Bundle ready: $bundleRoot"
Write-Host "Zip ready: $bundleZip"
