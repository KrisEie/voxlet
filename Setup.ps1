param(
    [ValidateSet('standard','full')][string]$Mode = 'standard',
    [switch]$SkipModels,
    [switch]$NoShortcut
)
$ErrorActionPreference = 'Stop'
$voxletRoot = $PSScriptRoot
$voxletRuntime = Join-Path $voxletRoot '.runtime'
New-Item -ItemType Directory -Path $voxletRuntime -Force | Out-Null
$voxletUv = Join-Path $voxletRuntime 'uv.exe'
function Invoke-VoxletUv([string[]]$Arguments) {
    & $voxletUv @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Setup stopped (uv exit code $LASTEXITCODE). You can run Setup.cmd again." }
}
if (-not (Test-Path -LiteralPath $voxletUv)) {
    Write-Host 'Downloading the pinned Python environment manager...'
    $voxletArchive = Join-Path $voxletRuntime 'uv-download.zip'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/astral-sh/uv/releases/download/0.12.23/uv-x86_64-pc-windows-msvc.zip' -OutFile $voxletArchive
    if ((Get-FileHash -LiteralPath $voxletArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne '75d05de6762778c31ee183398de7dd15093fad0ed90b1f236d8205ea5ec00c90') {
        throw 'The environment-manager download did not match its published checksum.'
    }
    Expand-Archive -LiteralPath $voxletArchive -DestinationPath $voxletRuntime -Force
    Remove-Item -LiteralPath $voxletArchive
}
$env:UV_CACHE_DIR = Join-Path $voxletRoot '.cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $voxletRuntime 'python'
$env:HF_HOME = Join-Path $voxletRoot '.cache/huggingface'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
Invoke-VoxletUv @('python','install','3.12')
foreach ($voxletEnv in @('desktop','speech')) {
    $voxletEnvPath = Join-Path $voxletRuntime $voxletEnv
    if (-not (Test-Path -LiteralPath (Join-Path $voxletEnvPath 'Scripts/python.exe'))) {
        Invoke-VoxletUv @('venv','--managed-python','--python','3.12',$voxletEnvPath)
    }
    Invoke-VoxletUv @('pip','install','--python',(Join-Path $voxletEnvPath 'Scripts/python.exe'),'-r',(Join-Path $voxletRoot "requirements-$voxletEnv.txt"))
}
if ($Mode -eq 'full') {
    foreach ($voxletEnv in @('qwen','chatterbox')) {
        $voxletEnvPath = Join-Path $voxletRuntime $voxletEnv
        if (-not (Test-Path -LiteralPath (Join-Path $voxletEnvPath 'Scripts/python.exe'))) {
            Invoke-VoxletUv @('venv','--managed-python','--python','3.12',$voxletEnvPath)
        }
        $voxletPython = Join-Path $voxletEnvPath 'Scripts/python.exe'
        Invoke-VoxletUv @('pip','install','--python',$voxletPython,'--index-url','https://download.pytorch.org/whl/cu118','torch==2.7.1','torchaudio==2.7.1')
        Invoke-VoxletUv @('pip','install','--python',$voxletPython,'-r',(Join-Path $voxletRoot "requirements-$voxletEnv.txt"))
        if ($voxletEnv -eq 'chatterbox') {
            Invoke-VoxletUv @('pip','install','--python',$voxletPython,'--no-deps','https://github.com/resemble-ai/chatterbox/archive/5de7a54aa4e5e2baadb0182dde554908b48b85c2.zip')
        }
        & $voxletPython (Join-Path $voxletRoot 'validate_cuda.py')
        if ($LASTEXITCODE -ne 0) { throw 'GPU cloning setup could not validate CUDA. Standard mode remains available.' }
    }
}
$voxletSpeechPython = Join-Path $voxletRuntime 'speech/Scripts/python.exe'
$voxletDownloadArgs = @((Join-Path $voxletRoot 'download_models.py'),'--mode',$Mode)
if ($SkipModels) { $voxletDownloadArgs += '--skip-models' }
& $voxletSpeechPython @voxletDownloadArgs
if ($LASTEXITCODE -ne 0) { throw 'A model or tool download failed. Run Setup.cmd again to resume.' }
if (-not $NoShortcut) {
    $voxletShell = New-Object -ComObject WScript.Shell
    $voxletShortcut = $voxletShell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Voxlet.lnk'))
    # Keep an existing Voxlet shortcut belonging to another installation.
    if (-not $voxletShortcut.TargetPath -or $voxletShortcut.WorkingDirectory -eq $voxletRoot) {
        if (Test-Path -LiteralPath (Join-Path $voxletRoot 'Voxlet.exe')) {
            $voxletShortcut.TargetPath = Join-Path $voxletRoot 'Voxlet.exe'
        } else {
            $voxletShortcut.TargetPath = Join-Path $voxletRuntime 'desktop/Scripts/pythonw.exe'
            $voxletShortcut.Arguments = '"' + (Join-Path $voxletRoot 'app.py') + '"'
        }
        $voxletShortcut.WorkingDirectory = $voxletRoot
        $voxletShortcut.IconLocation = Join-Path $voxletRoot 'voxlet.ico'
        $voxletShortcut.Save()
    }
}
Write-Host 'Setup complete. Open Voxlet.exe or double-click Start.cmd. First model use can take a while.'
