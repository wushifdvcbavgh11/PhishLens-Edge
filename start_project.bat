@echo off
title PhishLens Edge - Start Project
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\manage_project.ps1" -Action Start
set "PHISHLENS_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %PHISHLENS_EXIT%
