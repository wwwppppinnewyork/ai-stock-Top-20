@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在连接Wind并更新多因子选股数据，请保持Wind终端登录...
call daily_update.cmd
if errorlevel 1 (
  echo.
  echo 更新失败，请查看 logs\daily_update_error.log，并检查Wind终端登录状态及Python环境。
  pause
  exit /b 1
)
echo.
echo 更新完成。
start "" "查看结果\多因子选股看板.html"
pause
