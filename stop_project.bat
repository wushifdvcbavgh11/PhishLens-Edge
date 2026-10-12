@echo off
title PhishLens Edge - Stop Project
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\manage_project.ps1" -Action Stop
set "PHISHLENS_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %PHISHLENS_EXIT%
