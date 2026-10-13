@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  启动后端开发服务（FastAPI + uvicorn，热重载）
REM  用法：双击本文件，或在 cmd 中执行 scripts\dev-backend.bat
REM ============================================================

set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%..\"
set "BACKEND=%ROOT%backend\"

REM 解释器优先级：backend\.venv -> 工作空间根 .venv -> 系统 python
if exist "%BACKEND%.venv\Scripts\python.exe" (
    set "PY=%BACKEND%.venv\Scripts\python.exe"
) else if exist "%ROOT%..\.venv\Scripts\python.exe" (
    set "PY=%ROOT%..\.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo [INFO] 使用解释器：%PY%
echo [INFO] 后端目录  ：%BACKEND%
echo [INFO] 服务地址  ：http://127.0.0.1:8000/api/v1/docs
echo.

cd /d "%BACKEND%"
"%PY%" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

endlocal
