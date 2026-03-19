@echo off
setlocal
cd /d "%~dp0"

echo.
echo ========== Trading Lab Pro - Checklist and Run Test ==========
echo.

REM 1. Checklist
echo [1/5] Running checklist...
python scripts\check_setup.py
if errorlevel 1 (
    echo.
    echo Checklist failed. Fix errors and run run_test.bat again.
    pause
    exit /b 1
)
echo.

REM 2. Redis (optional)
echo [2/5] Checking Redis...
python scripts\ensure_redis.py
echo.

REM 3. Dependencies
echo [3/5] Installing dependencies...
pip install -q -r requirements.txt
if errorlevel 1 (
    echo Pip install failed. Check Python and requirements.txt
    pause
    exit /b 1
)
echo [OK] Dependencies installed.
echo.

REM 4. Seed DB
echo [4/5] Seeding DB...
python scripts\seed_db.py
echo.

REM 5. Quick test
echo [5/5] Running one cycle test...
python scripts\run_cycle.py --symbols BTC,ETH 2>nul
echo.

REM 6. Start API
echo ========== Starting API ==========
echo API: http://localhost:8000
echo Docs: http://localhost:8000/docs
echo Stop: Ctrl+C
echo.
uvicorn apps.api.server:app --host 127.0.0.1 --port 8000
pause
