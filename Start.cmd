@echo off
if exist "%~dp0Voxlet.exe" (
    start "" "%~dp0Voxlet.exe"
) else if exist "%~dp0.runtime\desktop\Scripts\pythonw.exe" (
    start "" "%~dp0.runtime\desktop\Scripts\pythonw.exe" "%~dp0app.py"
) else (
    echo Please run Setup.cmd first.
    pause
)
