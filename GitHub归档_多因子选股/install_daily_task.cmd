@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0安装每天21点自动更新.ps1"
exit /b %errorlevel%
