@echo off
setlocal EnableExtensions
chcp 65001 >nul
title 制药成本智能分析系统 - 主应用

echo ========================================
echo  制药成本智能分析系统 - 启动脚本
echo  ^(Anaconda Base 环境^)
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
    echo.
    pause
    exit /b 1
)
echo [环境] Python: %PYTHON%

REM --- 检查关键依赖（pymupdf 兼容 1.24.0 仅 fitz 与 >=1.24.3 双名） ---
"%PYTHON%" -c "import importlib.util as u, sys; m=['fastapi','uvicorn','pandas','docx','reportlab','chromadb','rank_bm25','jieba','httpx','dotenv','openai','langchain','langchain_community','langchain_text_splitters','numpy','pydantic','multipart']; miss=[x for x in m if u.find_spec(x) is None]; (u.find_spec('pymupdf') or u.find_spec('fitz')) or miss.append('pymupdf'); print('缺少依赖: '+', '.join(miss) if miss else '依赖完整'); sys.exit(bool(miss))"
if errorlevel 1 (
    echo [错误] 缺少必要依赖，请先安装:
    echo "%PYTHON%" -m pip install -r app\requirements.txt
    echo.
    pause
    exit /b 1
)

REM --- 预检 VC++ 2015-2022 x64 运行库（PyMuPDF/fitz 的原生 DLL 依赖）---
call :ensure_vc_runtime
if errorlevel 1 (
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

findstr /r "DASHSCOPE_API_KEY=." "app\.env" >nul 2>&1
if errorlevel 1 (
    echo [提示] 尚未配置百炼 Embedding API Key，知识库将无法构建向量索引
    echo        请编辑 app\.env 填入 DASHSCOPE_API_KEY
    echo.
)

REM --- 检查RPA mock server ---
netstat -ano | findstr ":8090 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [启动] RPA Mock Server ^(端口 8090^) ...
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
ping -n 2 127.0.0.1 >nul
goto :wait_for_rpa_loop

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