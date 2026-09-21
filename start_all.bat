@echo off
setlocal EnableExtensions
chcp 65001 >nul
title PharmaCost-AI - 一键启动

echo ========================================
echo  制药成本智能分析系统 - 一键启动
echo  (主应用 8000 + RPA服务 8090)
echo ========================================
echo.

cd /d "%~dp0"

REM --- Python: 优先使用交付环境，也允许评委通过环境变量或 PATH 覆盖 ---
if not defined PHARMACOST_PYTHON set "PHARMACOST_PYTHON=E:\Anacounda\python.exe"
set "PYTHON=%PHARMACOST_PYTHON%"
if not exist "%PYTHON%" (
    for %%P in (python.exe) do if not "%%~$PATH:P"=="" set "PYTHON=%%~$PATH:P"
)

REM --- 检查 Python 环境 ---
if not exist "%PYTHON%" (
    echo [错误] 未找到 Python 解释器。
    echo 请安装 Python 3.10+，或设置 PHARMACOST_PYTHON 为 python.exe 的完整路径。
    pause
    exit /b 1
)
echo [环境] Python: %PYTHON%

REM --- 自动安装依赖：评委首次双击时无需再输入 pip 命令 ---
echo [检查] Python 依赖...
"%PYTHON%" -c "import fastapi, uvicorn, pandas, docx, reportlab, chromadb, rank_bm25, jieba, httpx, dotenv, openai" >nul 2>&1
if errorlevel 1 (
    echo [安装] 首次运行，正在安装依赖；该步骤需要网络，可能需要数分钟...
    "%PYTHON%" -m pip install --disable-pip-version-check -r app\requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败。请检查网络、Python 权限后重试。
        pause
        exit /b 1
    )
)

REM --- 检查竞赛数据包，缺少时提前给出可操作提示 ---
if not exist "创灵境_考题模拟数据\01_成本明细数据" (
    echo [错误] 未找到竞赛数据包目录: 创灵境_考题模拟数据\01_成本明细数据
    echo 请将比赛数据包解压到项目根目录后，再双击 start_all.bat。
    pause
    exit /b 1
)

REM --- 检查端口，避免重复启动时出现不明确的端口占用报错 ---
call :ensure_port_free 8090 "RPA Mock"
if errorlevel 1 goto :failed
call :ensure_port_free 8000 "主应用"
if errorlevel 1 goto :failed

REM --- 第一步：启动 RPA (新窗口) ---
echo [1/2] 启动 RPA Mock Server (端口 8090) ...
start "RPA-Server" cmd /k "cd /d ""%~dp0"" && call start_rpa.bat"
call :wait_for_http "http://127.0.0.1:8090/health" "RPA Mock" 30
if errorlevel 1 goto :failed

REM --- 第二步：启动主应用并等待健康检查 ---
echo [2/2] 启动 主应用 (端口 8000) ...
start "PharmaCost-Web" cmd /k "cd /d ""%~dp0"" && call start.bat"
call :wait_for_http "http://127.0.0.1:8000/health/live" "主应用" 45
if errorlevel 1 goto :failed

echo.
echo ========================================
echo  启动成功
echo  前端页面: http://127.0.0.1:8000/
echo  API 文档:  http://127.0.0.1:8000/docs
echo  RPA Mock:  http://127.0.0.1:8090/docs
echo ========================================
if not defined PHARMACOST_NO_BROWSER start "" "http://127.0.0.1:8000/"
exit /b 0

:ensure_port_free
netstat -ano | findstr /r /c:":%~1 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [错误] %~2 端口 %~1 已被占用。请关闭已运行的服务后重试。
    exit /b 1
)
exit /b 0

:wait_for_http
set "WAIT_URL=%~1"
set "WAIT_NAME=%~2"
set /a WAIT_RETRIES=%~3
:wait_for_http_loop
"%PYTHON%" -c "import urllib.request; urllib.request.urlopen(r'%WAIT_URL%', timeout=2).read()" >nul 2>&1
if not errorlevel 1 (
    echo [就绪] %WAIT_NAME% 已启动
    exit /b 0
)
set /a WAIT_RETRIES-=1
if %WAIT_RETRIES% LEQ 0 (
    echo [错误] 等待 %WAIT_NAME% 启动超时。请查看对应服务窗口中的报错。
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :wait_for_http_loop

:failed
echo.
echo 启动未完成，请根据上方提示修复后重新运行。
pause
exit /b 1
