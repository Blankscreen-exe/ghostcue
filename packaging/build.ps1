# Builds GhostCue for release, into dist\:
#   GhostCue\                      the app folder, with GhostCue.exe
#   GhostCue-<version>-win64.zip   that folder, zipped
#   GhostCue-<version>-setup.exe   the installer, if Inno Setup 6 is installed
#
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Needs the project's .venv, with requirements.txt and packaging\requirements.txt installed.

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot
Set-Location $root

$version = [regex]::Match((Get-Content ghostcue\__init__.py -Raw), '__version__ = "(.+?)"').Groups[1].Value
Write-Host "Building GhostCue $version"

& .venv\Scripts\python -m PyInstaller packaging\ghostcue.spec --noconfirm --clean --distpath dist --workpath build
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$zip = "dist\GhostCue-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\GhostCue -DestinationPath $zip
Write-Host "Made $zip"

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if ($iscc) {
    $iscc = $iscc.Source
} else {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($iscc) {
    & $iscc /Qp "/DAppVersion=$version" packaging\ghostcue.iss
    if ($LASTEXITCODE) { throw "Inno Setup failed" }
    Write-Host "Made dist\GhostCue-$version-setup.exe"
} else {
    Write-Host "Inno Setup 6 is not installed, so there is no installer; the zip is complete without it."
}
