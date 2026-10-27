@echo off
chcp 65001 >nul
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_us_release.ps1"
if errorlevel 1 (
    echo Build failed. Review the error above.
    pause
    exit /b 1
)
echo Release package built under the workspace release-builds folder.
pause
