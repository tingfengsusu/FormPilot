@echo off
rem 启动飞书机器人（在这个窗口里常驻；关掉窗口 = 停止机器人）
rem 双击本文件即可运行；首次需先配好 config\gateway.yaml 的飞书凭据
chcp 65001 >nul
cd /d "%~dp0.."
echo === FormPilot 飞书机器人 ===
echo 日志会打印在本窗口；Ctrl+C 或关闭窗口即停止。
echo.
".venv\Scripts\python.exe" -u -X utf8 tools\feishu_bot.py
echo.
echo 机器人已退出。
pause
