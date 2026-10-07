param([string]$Python = (Join-Path $PSScriptRoot '.runtime/desktop/Scripts/python.exe'))
$ErrorActionPreference = 'Stop'
$voxletBuildRoot = $PSScriptRoot
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run Setup.ps1 -SkipModels -NoShortcut first.' }
& $Python -c 'import PyInstaller'
if ($LASTEXITCODE -ne 0) {
    & (Join-Path $voxletBuildRoot '.runtime/uv.exe') pip install --python $Python 'pyinstaller==6.22.3'
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the release builder.' }
}
$voxletPythonSite = & $Python (Join-Path $voxletBuildRoot 'scripts/prepare_build.py')
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare Windows text-field integration.' }
$voxletArgs = @('-m','PyInstaller','--noconfirm','--onedir','--windowed','--name','Voxlet',
    '--icon',(Join-Path $voxletBuildRoot 'voxlet.ico'),
    '--distpath',(Join-Path $voxletBuildRoot 'dist'),
    '--workpath',(Join-Path $voxletBuildRoot 'build'),
    '--specpath',$voxletBuildRoot,
    '--hidden-import','comtypes.gen.UIAutomationClient',
    '--hidden-import','comtypes.gen._944DE083_8FB8_45CF_BCB7_C477ACB2F897_0_1_0',
    '--collect-all','sounddevice',
    '--exclude-module','PySide6.QtWebEngineCore','--exclude-module','PySide6.QtWebEngineWidgets',
    '--exclude-module','PySide6.QtQml','--exclude-module','PySide6.QtQuick')
foreach ($voxletAsset in @('voxlet.ico','voxlet.png','check.svg','chevron.svg')) {
    $voxletArgs += @('--add-data',((Join-Path $voxletBuildRoot $voxletAsset)+';.'))
}
$voxletArgs += (Join-Path $voxletBuildRoot 'app.py')
& $Python @voxletArgs
if ($LASTEXITCODE -ne 0) { throw 'The desktop build failed.' }
$voxletPackage = Join-Path $voxletBuildRoot 'dist/Voxlet'
Get-ChildItem -LiteralPath (Join-Path $voxletPythonSite 'PySide6') -File |
    Where-Object { $_.Name -match '^(MSVCP140.*|VCRUNTIME140.*)\.dll$' } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $voxletPackage '_internal') -Force }
$voxletWrongIcu = Join-Path $voxletPackage '_internal/icuuc.dll'
if (Test-Path -LiteralPath $voxletWrongIcu) { Remove-Item -LiteralPath $voxletWrongIcu }
# Explicit allowlist: never package a live app folder, settings, caches or recordings.
$voxletPublicFiles = @('app.py','ui.py','engines.py','native.py','lifecycle.py','portable_config.py',
    'media.py','playback.py','reference_audio.py','samples.py','worker.py','qwen_worker.py','chatterbox_worker.py',
    'model_registry.py','validate_cuda.py',
    'download_models.py','Setup.ps1','Setup.cmd','Start.cmd','README.md','LICENSE','THIRD_PARTY_NOTICES.md','CHANGELOG.md',
    'requirements-desktop.txt','requirements-speech.txt','requirements-qwen.txt','requirements-chatterbox.txt',
    'voxlet.ico','voxlet.png','check.svg','chevron.svg','docs/reader.png')
foreach ($voxletFile in $voxletPublicFiles) {
    New-Item -ItemType Directory -Path (Split-Path -Parent (Join-Path $voxletPackage $voxletFile)) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $voxletBuildRoot $voxletFile) -Destination (Join-Path $voxletPackage $voxletFile) -Force
}
& $Python (Join-Path $voxletBuildRoot 'scripts/collect_licenses.py') $voxletPackage
if ($LASTEXITCODE -ne 0) { throw 'Could not collect dependency notices.' }
New-Item -ItemType Directory -Path (Join-Path $voxletBuildRoot 'release') -Force | Out-Null
& $Python (Join-Path $voxletBuildRoot 'scripts/package_release.py') $voxletPackage (Join-Path $voxletBuildRoot 'release/Voxlet-0.1.0-windows-x64.zip')
if ($LASTEXITCODE -ne 0) { throw 'The clean release archive could not be created.' }
