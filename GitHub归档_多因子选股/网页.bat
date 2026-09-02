@echo off
cd /d "%~dp0"
python "src\launch_weight_control.py" portal
if errorlevel 1 pause
