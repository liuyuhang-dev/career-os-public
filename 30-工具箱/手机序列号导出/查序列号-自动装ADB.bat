@echo off
chcp 936 >nul 2>&1
title Phone Serial Number (auto install adb)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get-phone-serial.ps1" -DownloadAdb %*
echo.
echo ---- press any key to close ----
pause >nul
