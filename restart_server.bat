@echo off
title PhishLens Edge - Restart API
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\manage_project.ps1" -Action Restart -Service Api
set "PHISHLENS_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %PHISHLENS_EXIT%
