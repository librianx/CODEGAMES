param(
  [string]$Input = "README_USER.md",
  [string]$Output = "README_USER.docx"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$inPath = Join-Path $root $Input
$outPath = Join-Path $root $Output

if (!(Test-Path -LiteralPath $inPath)) {
  throw "Input not found: $inPath"
}

function Find-Pandoc {
  $cmd = Get-Command pandoc.exe -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source) { return $cmd.Source }

  # WinGet default extraction location (user scope)
  $candidate = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\JohnMacFarlane.Pandoc_Microsoft.Winget.Source_8wekyb3d8bbwe"
  if (Test-Path -LiteralPath $candidate) {
    $found = Get-ChildItem -LiteralPath $candidate -Filter pandoc.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.FullName }
  }

  throw "pandoc.exe not found. Install it first: winget install --id JohnMacFarlane.Pandoc -e"
}

$pandoc = Find-Pandoc

Write-Host "pandoc: $pandoc"
Write-Host "input : $inPath"
Write-Host "output: $outPath"

& $pandoc `
  --from markdown `
  --to docx `
  --standalone `
  --output $outPath `
  $inPath

Write-Host "Done."

