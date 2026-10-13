@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  启动前端开发服务（Vite，五入口，/api 代理到 127.0.0.1:8000）
REM  用法：双击本文件，或在 cmd 中执行 scripts\dev-frontend.bat
REM  前置：需先在 frontend 目录执行 npm install
REM ============================================================

set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%..\"
set "FRONTEND=%ROOT%frontend\"

if not exist "%FRONTEND%node_modules" (
    echo [WARN] 未检测到 node_modules，正在安装依赖...
    cd /d "%FRONTEND%"
    call npm install
)

echo [INFO] 前端目录：%FRONTEND%
echo [INFO] 访问地址：http://127.0.0.1:5173/
echo.

cd /d "%FRONTEND%"
call npm run dev

endlocal
