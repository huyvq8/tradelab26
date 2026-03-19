@echo off
cd /d "%~dp0"
echo.
echo ========== Trading Lab Pro - Check / Start Redis ==========
echo.
python scripts\ensure_redis.py
echo.
pause
