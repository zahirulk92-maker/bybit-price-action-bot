@echo off
setlocal
title Bybit Price Action Bot - Demo Orders
cd /d "%~dp0"

echo.
echo =====================================================
echo   Bybit Price Action Bot - DEMO ORDER MODE
echo   Live trading is forced OFF. Demo orders only.
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

echo [SETUP] Installing/updating dependencies...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :failed

if not exist ".env" (
    if exist ".env - Copy.example" (
        copy /Y ".env - Copy.example" ".env" >nul
        echo [SETUP] Created .env from your existing local configuration.
    ) else (
        copy /Y ".env.example" ".env" >nul
        echo [SETUP] Created .env from .env.example.
    )
)

rem Demo-only overrides. Values in .env cannot override these settings.
set "BYBIT_DEMO=true"
set "ENABLE_ORDER_PLACEMENT=true"
set "RUN_ENGINE_IN_WEB=true"
set "DASHBOARD_ALLOW_INSECURE_LOCAL=true"
set "PYTHONUTF8=1"

echo.
echo [SAFE MODE] Bybit Demo: ON
echo [MODE] Demo order placement: ON
echo [MODE] Live trading: FORCED OFF
echo [DASHBOARD] http://127.0.0.1:8000
echo [STOP] Press Ctrl+C in this window.
echo.

start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8000'"
".venv\Scripts\python.exe" -m uvicorn price_action_bot.web:app --host 127.0.0.1 --port 8000
exit /b %errorlevel%

:failed
echo.
echo [ERROR] Setup failed. Read the message above for details.
pause
exit /b 1
