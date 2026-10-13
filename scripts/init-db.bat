@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  初始化 / 校验数据库（幂等）
REM  用法：
REM     scripts\init-db.bat           仅建库
REM     scripts\init-db.bat --verify  建库并校验表与索引
REM     scripts\init-db.bat --rebuild --verify  删除旧库后重建并校验
REM ============================================================

set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%..\"
set "BACKEND=%ROOT%backend\"

if exist "%BACKEND%.venv\Scripts\python.exe" (
    set "PY=%BACKEND%.venv\Scripts\python.exe"
) else if exist "%ROOT%..\.venv\Scripts\python.exe" (
    set "PY=%ROOT%..\.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

cd /d "%BACKEND%"
"%PY%" scripts\init_db.py %*

endlocal
