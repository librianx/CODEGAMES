param(
  [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
Set-Location "E:\CODEGAMES\VIVY"

if (!(Test-Path ".venv")) {
  py -m venv .venv
}

.\.venv\Scripts\activate

pip install -r requirements.txt
pip install pyinstaller pillow

$iconPng = "static\images\VIVYstatr.png"
$iconIco = "release\vivy.ico"
$iconPy = "release\_make_icon.py"

@"
from PIL import Image
img = Image.open(r"E:\\CODEGAMES\\VIVY\\static\\images\\VIVYstatr.png").convert("RGBA")
sizes = [(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)]
img.save(r"E:\\CODEGAMES\\VIVY\\release\\vivy.ico", format="ICO", sizes=sizes)
print("icon generated")
"@ | Set-Content -Path $iconPy -Encoding UTF8

py $iconPy
Remove-Item $iconPy -Force

if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }

py -m PyInstaller --noconfirm --windowed --name "VIVY" --icon "$iconIco" `
  --add-data "static\\images;static\\images" `
  --add-data ".env.example;." `
  desktop_pet.py

$releaseDir = "dist\VIVY-release"
if (Test-Path $releaseDir) { Remove-Item -Recurse -Force $releaseDir }
New-Item -ItemType Directory -Path $releaseDir | Out-Null

Copy-Item "dist\VIVY\*" $releaseDir -Recurse -Force
Copy-Item ".env.example" "$releaseDir\.env.example" -Force
Copy-Item "README.md" "$releaseDir\README.md" -Force
Copy-Item "start_desktop.bat" "$releaseDir\start_desktop.bat" -Force

$zipPath = "dist\VIVY-release-v$Version.zip"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path "$releaseDir\*" -DestinationPath $zipPath

Write-Host "Done."
Write-Host "- Folder: $releaseDir"
Write-Host "- Zip: $zipPath"
Write-Host "- Inno script: release\\VIVY.iss"
Write-Host "Next: open release\\VIVY.iss in Inno Setup and Compile."
