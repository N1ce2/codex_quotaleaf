param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& $Python -m venv .build-venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create build environment' }
$buildPython = Join-Path $PSScriptRoot '.build-venv\Scripts\python.exe'
& $buildPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install build dependencies' }
& $buildPython -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
& $buildPython -m PyInstaller --noconfirm codex_quotaleaf.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
& $buildPython package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed' }
Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'dist\codex_quotaleaf.exe') -Algorithm SHA256
