@echo off
chcp 65001 >nul
setlocal
echo Building the complete US trainer release, including the optional AI assistant.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_us_release.ps1"
if errorlevel 1 (
    echo Build failed. Review the error above.
    pause
    exit /b 1
)
pause
