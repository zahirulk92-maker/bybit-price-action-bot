@echo off
setlocal
title Bybit Price Action Bot - Local Demo
cd /d "%~dp0"

echo.
echo =====================================================
echo   Bybit Price Action Bot - LOCAL DEMO / SIGNAL ONLY
echo =====================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    echo Install Python 3.12, then run this file again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :failed
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, pybit, psycopg, price_action_bot" >nul 2>nul
if errorlevel 1 (
    echo [SETUP] Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install -e .
    if errorlevel 1 goto :failed
) else (
    echo [SETUP] Dependencies are ready.
)

if not exist ".env" (
    if exist ".env - Copy.example" (
        copy /Y ".env - Copy.example" ".env" >nul
        echo [SETUP] Created .env from your existing local configuration.
    ) else (
        copy /Y ".env.example" ".env" >nul
        echo [SETUP] Created .env from .env.example.
    )
)

set "PYTHONUTF8=1"
".venv\Scripts\python.exe" -m price_action_bot.local
set "SERVER_EXIT=%ERRORLEVEL%"
if not "%SERVER_EXIT%"=="0" (
    echo.
    echo [ERROR] The server stopped with exit code %SERVER_EXIT%.
    pause
)
exit /b %SERVER_EXIT%

:failed
echo.
echo [ERROR] Setup failed. Read the message above for details.
pause
exit /b 1
