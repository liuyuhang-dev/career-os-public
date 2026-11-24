@echo off
chcp 936 >nul 2>&1
title Phone Serial Number
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get-phone-serial.ps1" %*
echo.
echo ---- press any key to close ----
pause >nul
