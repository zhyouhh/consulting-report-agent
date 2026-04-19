@echo off
setlocal
chcp 65001 >nul

if /I "%~1"=="--no-pause" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" -NoPause
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
)

exit /b %errorlevel%
