@echo off
rem JobMap 一键启动脚本（Windows）
cd /d "%~dp0.."
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 run.py %*
) else (
    python run.py %*
)
pause
