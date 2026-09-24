@echo off
setlocal EnableExtensions
chcp 65001 >nul
title RPA Mock Server

echo ========================================
echo  RPA Mock Server - 端口 8090
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

REM --- 预检 VC++ 2015-2022 x64 运行库（PyMuPDF/fitz 的原生 DLL 依赖）---
call :ensure_vc_runtime
if errorlevel 1 (
    pause
    exit /b 1
)

echo [启动] RPA Mock Server on http://127.0.0.1:8090
echo [停止] 按 Ctrl+C
echo.

cd rpa_mock
"%PYTHON%" mock_rpa_server.py

pause
exit /b 0

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