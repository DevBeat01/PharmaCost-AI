@echo off
setlocal EnableExtensions
chcp 65001 >nul
title PharmaCost-AI - 一键启动

echo ========================================
echo  制药成本智能分析系统 - 一键启动
echo  ^(主应用 8000 + RPA服务 8090^)
echo ========================================
echo.

cd /d "%~dp0"

REM --- 定位 Python 解释器（环境变量 / 常见安装路径 / py 启动器 / PATH）---
set "PYTHON="
if defined PHARMACOST_PYTHON if exist "%PHARMACOST_PYTHON%" set "PYTHON=%PHARMACOST_PYTHON%"
REM --- 优先使用 Python 3.11（chromadb 依赖的 chroma-hnswlib 仅提供到 cp311 的 Windows 预编译轮子，3.12/3.13 无法安装）---
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYTHON if exist "C:\Python311\python.exe" set "PYTHON=C:\Python311\python.exe"
if not defined PYTHON if exist "C:\Program Files\Python311\python.exe" set "PYTHON=C:\Program Files\Python311\python.exe"
if not defined PYTHON if exist "E:\Anacounda\python.exe" set "PYTHON=E:\Anacounda\python.exe"
if not defined PYTHON if exist "C:\ProgramData\Anaconda3\python.exe" set "PYTHON=C:\ProgramData\Anaconda3\python.exe"
if not defined PYTHON if exist "C:\ProgramData\miniconda3\python.exe" set "PYTHON=C:\ProgramData\miniconda3\python.exe"
if not defined PYTHON if exist "%USERPROFILE%\anaconda3\python.exe" set "PYTHON=%USERPROFILE%\anaconda3\python.exe"
if not defined PYTHON if exist "%USERPROFILE%\miniconda3\python.exe" set "PYTHON=%USERPROFILE%\miniconda3\python.exe"
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PYTHON if exist "C:\Python313\python.exe" set "PYTHON=C:\Python313\python.exe"
if not defined PYTHON if exist "C:\Python312\python.exe" set "PYTHON=C:\Python312\python.exe"
if not defined PYTHON if exist "C:\Python311\python.exe" set "PYTHON=C:\Python311\python.exe"
if not defined PYTHON if exist "C:\Python310\python.exe" set "PYTHON=C:\Python310\python.exe"

REM --- Python 启动器 py -3.11：优先选用 3.11，自动跳过商店占位程序 ---
if not defined PYTHON (
    for /f "delims=" %%I in ('py -3.11 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYTHON set "PYTHON=%%I"
)

REM --- PATH 中的 python，排除 WindowsApps 商店占位程序 ---
if not defined PYTHON (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PYTHON echo %%I|findstr /i "\\WindowsApps\\" >nul || set "PYTHON=%%I"
    )
)

REM --- 校验 Python ---
if not defined PYTHON (
    echo [错误] 未找到可用的 Python 解释器。
    echo 请安装 Python 3.10+，或设置环境变量 PHARMACOST_PYTHON 指向 python.exe 的完整路径。
    pause
    exit /b 1
)
echo [环境] Python: %PYTHON%

REM --- 自动安装依赖：评委首次双击时无需再输入 pip 命令 ---
echo [检查] Python 依赖...
"%PYTHON%" -c "import importlib.util as u, sys; m=['fastapi','uvicorn','pandas','docx','reportlab','chromadb','rank_bm25','jieba','httpx','dotenv','openai','langchain','langchain_community','langchain_text_splitters','numpy','pydantic','multipart']; miss=[x for x in m if u.find_spec(x) is None]; (u.find_spec('pymupdf') or u.find_spec('fitz')) or miss.append('pymupdf'); print('缺少依赖: '+', '.join(miss) if miss else '依赖完整'); sys.exit(bool(miss))"
if errorlevel 1 (
    echo [安装] 首次运行，正在安装依赖；该步骤需要网络，可能需要数分钟...
    "%PYTHON%" -m pip install --disable-pip-version-check -r app\requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败。请检查网络、Python 权限后重试。
        pause
        exit /b 1
    )
)

REM --- 预检 VC++ 2015-2022 x64 运行库（PyMuPDF/fitz 的原生 DLL 依赖）---
call :ensure_vc_runtime
if errorlevel 1 goto :failed

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
echo [1/2] 启动 RPA Mock Server ^(端口 8090^) ...
start "RPA-Server" cmd /k "cd /d ""%~dp0"" && call start_rpa.bat"
call :wait_for_http "http://127.0.0.1:8090/health" "RPA Mock" 30
if errorlevel 1 goto :failed

REM --- 第二步：启动主应用并等待健康检查 ---
echo [2/2] 启动 主应用 ^(端口 8000^) ...
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
ping -n 2 127.0.0.1 >nul
goto :wait_for_http_loop

:ensure_vc_runtime
REM --- 预检 VC++ 2015-2022 x64 运行库：PyMuPDF(fitz) 的原生 DLL 依赖 ---
if exist "%SystemRoot%\System32\vcruntime140.dll" if exist "%SystemRoot%\System32\msvcp140.dll" exit /b 0
echo [预检] 未检测到 VC++ 2015-2022 运行库 ^(PyMuPDF 需要^)，准备自动安装...
set "VC_REDIST=%~dp0tools\vc_redist.x64.exe"
if not exist "%VC_REDIST%" set "VC_REDIST=%~dp0vc_redist.x64.exe"
if exist "%VC_REDIST%" goto :vc_redist_install
set "VC_REDIST=%TEMP%\vc_redist.x64.exe"
if exist "%VC_REDIST%" del /q "%VC_REDIST%" >nul 2>&1
echo [下载] 正在从微软官方获取 vc_redist.x64.exe ...
curl -L -s -o "%VC_REDIST%" "https://aka.ms/vs/17/release/vc_redist.x64.exe" >nul 2>&1
if not exist "%VC_REDIST%" powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $env:TEMP\vc_redist.x64.exe } catch { exit 1 }" >nul 2>&1
if not exist "%VC_REDIST%" (
    echo [错误] 无法获取 VC++ 运行库安装包。
    echo 请手动下载安装后重试: https://aka.ms/vs/17/release/vc_redist.x64.exe
    exit /b 1
)
:vc_redist_install
echo [安装] 正在安装 VC++ 运行库，请稍候...
"%VC_REDIST%" /install /quiet /norestart
set /a VC_WAIT=30
:vc_redist_wait
if exist "%SystemRoot%\System32\vcruntime140.dll" if exist "%SystemRoot%\System32\msvcp140.dll" (
    echo [就绪] VC++ 运行库已可用
    exit /b 0
)
set /a VC_WAIT-=1
if %VC_WAIT% LEQ 0 (
    echo [错误] VC++ 运行库安装后仍未检测到 DLL。
    echo 请手动下载安装后重试: https://aka.ms/vs/17/release/vc_redist.x64.exe
    exit /b 1
)
ping -n 2 127.0.0.1 >nul
goto :vc_redist_wait

:failed
echo.
echo 启动未完成，请根据上方提示修复后重新运行。
pause
exit /b 1