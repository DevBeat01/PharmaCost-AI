@echo off
chcp 65001 >nul
title 制药成本智能分析系统 - 主应用

echo ========================================
echo  制药成本智能分析系统 - 启动脚本
echo  (Anaconda Base 环境)
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

findstr /r "DASHSCOPE_API_KEY=.\+" "app\.env" >nul 2>&1
if errorlevel 1 (
    echo [提示] 尚未配置百炼 Embedding API Key，知识库将无法构建向量索引
    echo        请编辑 app\.env 填入 DASHSCOPE_API_KEY
    echo.
)

REM --- 检查RPA mock server ---
netstat -ano | findstr ":8090 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [启动] RPA Mock Server (端口 8090) ...
    start "RPA-Server" cmd /k "cd /d ""%~dp0"" && call start_rpa.bat"
    call :wait_for_rpa 30
    if errorlevel 1 (
        echo [警告] RPA服务启动超时，整改任务派发可能失败
        echo        可查看 RPA-Server 窗口中的错误信息
    )
    echo.
)

if not defined RPA_BASE_URL set "RPA_BASE_URL=http://127.0.0.1:8090"

REM --- 启动主应用 ---
echo [启动] 主应用 uvicorn on http://127.0.0.1:8000
echo [启动] 知识库构建在后台异步运行（使用百炼 Embedding API）
echo [停止] 按 Ctrl+C
echo.

cd app
"%PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 8000

pause
exit /b 0

:wait_for_rpa
set /a RPA_RETRIES=%~1
:wait_for_rpa_loop
"%PYTHON%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=2).read()" >nul 2>&1
if not errorlevel 1 (
    echo [就绪] RPA Mock Server 已启动
    exit /b 0
)
set /a RPA_RETRIES-=1
if %RPA_RETRIES% LEQ 0 exit /b 1
timeout /t 1 /nobreak >nul
goto :wait_for_rpa_loop
