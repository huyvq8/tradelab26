@echo off
cd /d "%~dp0"

echo Starting Trading Lab Pro - all components...
echo.

REM Start API (port 8000)
start "Trading Lab - API" cmd /k "cd /d "%~dp0" && uvicorn apps.api.server:app --host 127.0.0.1 --port 8000"
timeout /t 2 /nobreak >nul

REM Start Worker (cycle + report + Telegram)
start "Trading Lab - Worker" cmd /k "cd /d "%~dp0" && python apps/worker/runner.py"
timeout /t 1 /nobreak >nul

REM Start Dashboard (port 8501)
start "Trading Lab - Dashboard" cmd /k "cd /d "%~dp0" && streamlit run apps/dashboard/app.py --server.port 8501 --server.headless true"
timeout /t 3 /nobreak >nul

REM Open browser to dashboard
start http://localhost:8501

echo.
echo Dashboard: http://localhost:8501
echo API:      http://localhost:8000
echo Close the 3 opened windows to stop all.
pause
