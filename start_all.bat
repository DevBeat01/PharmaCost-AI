@echo off
chcp 65001 >nul
title 一键启动全部服务

echo ========================================
echo  制药成本智能分析系统 - 一键启动
echo  (主应用 8000 + RPA服务 8090)
echo ========================================
echo.

cd /d "%~dp0"

REM --- 设置 Anaconda Python 路径 ---
set PYTHON=E:\Anacounda\python.exe

REM --- 检查 Anaconda 环境 ---
if not exist "%PYTHON%" (
    echo [错误] 未找到 Anaconda Python: %PYTHON%
    echo 请确认 E:\Anacounda 目录存在
    pause
    exit /b 1
)

REM --- 第一步：启动 RPA (新窗口) ---
echo [1/2] 启动 RPA Mock Server (端口 8090) ...
start "RPA-Server" cmd /k "cd /d ""%~dp0"" && call start_rpa.bat"

timeout /t 3 /nobreak >nul

REM --- 第二步：启动主应用 (当前窗口) ---
echo [2/2] 启动 主应用 (端口 8000) ...
echo.
echo === 服务地址 ===
echo   前端页面:   http://127.0.0.1:8000/
echo   API文档:    http://127.0.0.1:8000/docs
echo   RPA Mock:   http://127.0.0.1:8090/docs
echo.
echo 按任意键启动主应用 ...
pause >nul

call start.bat
