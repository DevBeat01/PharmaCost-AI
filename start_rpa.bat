@echo off
chcp 65001 >nul
title RPA Mock Server

echo ========================================
echo  RPA Mock Server - 端口 8090
echo ========================================
echo.

cd /d "%~dp0"

REM --- 设置 Python 路径；可由一键启动器通过环境变量覆盖 ---
if not defined PHARMACOST_PYTHON set "PHARMACOST_PYTHON=E:\Anacounda\python.exe"
set "PYTHON=%PHARMACOST_PYTHON%"

REM --- 检查 Python 环境 ---
if not exist "%PYTHON%" (
    echo [错误] 未找到 Python: %PYTHON%
    echo 请确认 Python 路径存在，或设置 PHARMACOST_PYTHON。
    pause
    exit /b 1
)

echo [启动] RPA Mock Server on http://127.0.0.1:8090
echo [停止] 按 Ctrl+C
echo.

cd rpa_mock
"%PYTHON%" mock_rpa_server.py

pause
