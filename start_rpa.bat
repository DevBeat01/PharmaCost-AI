@echo off
chcp 65001 >nul
title RPA Mock Server

echo ========================================
echo  RPA Mock Server - 端口 8090
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

echo [启动] RPA Mock Server on http://127.0.0.1:8090
echo [停止] 按 Ctrl+C
echo.

cd rpa_mock
"%PYTHON%" mock_rpa_server.py

pause
