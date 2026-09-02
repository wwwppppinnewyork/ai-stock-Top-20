@echo off
rem 保留旧入口以兼容原有快捷方式，实际执行每日更新脚本。
call "%~dp0daily_update.cmd"
exit /b %errorlevel%
