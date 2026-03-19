@echo off
cd /d "%~dp0"
echo.
echo Dashboard se mo tai: http://localhost:8501
echo Dong cua so hoac Ctrl+C de dung.
echo.
streamlit run apps/dashboard/app.py --server.port 8501
pause
