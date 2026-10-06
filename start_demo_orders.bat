@echo off
setlocal
title Bybit Price Action Bot - DEMO ORDERS
cd /d "%~dp0"

echo.
echo =====================================================
echo   Bybit Price Action Bot - DEMO ORDER MODE
echo =====================================================
echo   Live trading is forced OFF. Demo orders only.
echo =====================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run start.bat once first.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] .env not found. Add Bybit demo API credentials first.
    pause
    exit /b 1
)

rem Explicit demo-only execution overrides local .env execution mode.
set "BYBIT_DEMO=true"
set "ENABLE_ORDER_PLACEMENT=true"
set "RUN_ENGINE_IN_WEB=true"
set "DASHBOARD_ALLOW_INSECURE_LOCAL=true"
set "PYTHONUTF8=1"

echo [SAFE MODE] Bybit Demo: ON
echo [MODE] Demo order placement: ON
echo [MODE] Live trading: FORCED OFF
echo [DASHBOARD] http://127.0.0.1:8000
echo [STOP] Press Ctrl+C in this window.
echo.

start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8000'"
".venv\Scripts\python.exe" -m uvicorn price_action_bot.web:app --host 127.0.0.1 --port 8000
exit /b %errorlevel%
