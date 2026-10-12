@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_and_start.ps1"
set "PHISHLENS_EXIT=%ERRORLEVEL%"
pause
exit /b %PHISHLENS_EXIT%
