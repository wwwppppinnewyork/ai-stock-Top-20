@echo off
cd /d "%~dp0"
powershell -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0设置OpenAI密钥并打开公司研究.ps1"

