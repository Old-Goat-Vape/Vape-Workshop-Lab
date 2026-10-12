@echo off
setlocal EnableExtensions
rem LAN device testing — same Wi-Fi only. NOT for public internet.
set "FLASK_USE_RELOADER=0"
set "PYTHONUTF8=1"
set "TVW_ENABLE_LAB_MODULES=1"
set "FLASK_RUN_HOST=0.0.0.0"
set "FLASK_RUN_PORT=5055"
cd /d "%~dp0"

echo.
echo ========================================
echo   TVW Calculator — DEVICE TESTING (LAN)
echo   Port 5055, bind 0.0.0.0 (private Wi-Fi)
echo ========================================
echo.

if not exist "app.py" goto err_app
if not exist "venv\Scripts\python.exe" goto err_venv

echo Starting dev server for phone/tablet testing...
echo See README_DEVICE_TESTING.txt for ipconfig and firewall steps.
echo.
"%CD%\venv\Scripts\python.exe" app.py --host 0.0.0.0 --port 5055
goto end_ok

:err_app
echo ERROR: app.py not found in "%CD%"
goto end_fail

:err_venv
echo ERROR: venv not found. Run Start_TVW_Calculator.bat once to create it.
goto end_fail

:end_fail
pause
endlocal
exit /b 1

:end_ok
pause
endlocal
exit /b 0
