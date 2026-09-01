@echo off
chcp 65001 >nul
title 制药成本智能分析系统 - 主应用

echo ========================================
echo  制药成本智能分析系统 - 启动脚本
echo  (Anaconda Base 环境)
echo ========================================
echo.

cd /d "%~dp0"

REM --- 设置 Anaconda Python 路径 ---
set PYTHON=E:\Anacounda\python.exe

REM --- 检查 Anaconda 环境 ---
if not exist "%PYTHON%" (
    echo [错误] 未找到 Anaconda Python: %PYTHON%
    echo 请确认 E:\Anacounda 目录存在
    echo.
    pause
    exit /b 1
)

REM --- 检查关键依赖 ---
"%PYTHON%" -c "import fastapi, pandas, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [错误] 缺少必要依赖，请先安装:
    echo "%PYTHON%" -m pip install -r app\requirements.txt
    echo.
    pause
    exit /b 1
)

REM --- 检查竞赛数据包 ---
if not exist "创灵境_考题模拟数据\01_成本明细数据" (
    echo [错误] 未找到竞赛数据包目录: 创灵境_考题模拟数据
    echo 请从比赛材料获取数据包，并解压到项目根目录后再启动。
    echo.
    pause
    exit /b 1
)

REM --- 检查.env中的API Key ---
findstr /r "DEEPSEEK_API_KEY=sk-" "app\.env" >nul 2>&1
if errorlevel 1 (
    echo [提示] 尚未配置 DeepSeek 主模型 API Key，将使用 MiMo 备用模型
    echo        如需使用 DeepSeek，请编辑 app\.env 填入 DEEPSEEK_API_KEY
    echo.
)

REM --- 检查RPA mock server ---
netstat -ano | findstr ":8090 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [警告] RPA服务(8090)未启动，整改任务派发将失败
    echo        如需RPA功能，请另开terminal运行: start_rpa.bat
    echo.
)

REM --- 启动主应用 ---
echo [启动] 主应用 uvicorn on http://127.0.0.1:8000
echo [启动] 知识库构建在后台异步运行（首次启动需下载模型约80MB）
echo [停止] 按 Ctrl+C
echo.

cd app
"%PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 8000

pause
