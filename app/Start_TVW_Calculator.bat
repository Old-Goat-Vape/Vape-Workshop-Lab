@echo off
setlocal EnableExtensions
rem GOTO-only. No echo lines starting with parenthesis. Test in cmd.exe.
rem Dry-run: cmd.exe, cd /d path\to\app, run this file.
rem Python: prefers py -3.13 then py -3.12; warns on 3.14+ for fallbacks. See README_WINDOWS_START_HERE.txt.
set "FLASK_USE_RELOADER=0"
set "PYTHONUTF8=1"
set "TVW_ENABLE_LAB_MODULES=1"
set "TVW_URL=http://127.0.0.1:5000/recipes"
cd /d "%~dp0"

echo.
echo ========================================
echo   The Vape Workshop Calculator
echo ========================================
echo Lab bridge TVW_ENABLE_LAB_MODULES=%TVW_ENABLE_LAB_MODULES%
echo See LAUNCH_QA_ENV.txt for TVW-only mode and env vars.
echo.

if not exist "app.py" goto err_app
if not exist "requirements.txt" goto err_req

set "VENV_PY=%CD%\venv\Scripts\python.exe"
set "PYCREATE="

py -3.13 -c "import sys" >nul 2>&1
if errorlevel 1 goto pick_py312
set "PYCREATE=py -3.13"
goto pick_py_done
:pick_py312
py -3.12 -c "import sys" >nul 2>&1
if errorlevel 1 goto pick_python_exe
set "PYCREATE=py -3.12"
goto pick_py_done
:pick_python_exe
where python >nul 2>&1
if errorlevel 1 goto pick_py_launcher
python -c "import sys; v=sys.version_info; raise SystemExit(0 if v[0]*100+v[1]<=313 else 1)" >nul 2>&1
if errorlevel 1 goto warn_python_high
set "PYCREATE=python"
goto pick_py_done
:warn_python_high
echo WARNING: Default Python is 3.14 or newer. Use Python 3.12 or 3.13 for best compatibility.
echo Continuing. If pip install fails install Python 3.12 from python.org.
echo.
set "PYCREATE=python"
goto pick_py_done
:pick_py_launcher
where py >nul 2>&1
if errorlevel 1 goto err_python
py -3 -c "import sys; v=sys.version_info; raise SystemExit(0 if v[0]*100+v[1]<=313 else 1)" >nul 2>&1
if errorlevel 1 goto warn_py3_high
set "PYCREATE=py -3"
goto pick_py_done
:warn_py3_high
echo WARNING: py -3 selects Python 3.14 or newer. Prefer py -3.12 or py -3.13.
set "PYCREATE=py -3"
goto pick_py_done
:pick_py_done

if exist "%VENV_PY%" goto have_venv
echo First run: creating virtual environment venv...
%PYCREATE% -m venv venv
if errorlevel 1 goto err_venv
:have_venv

echo Upgrading pip...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto err_pip

echo Installing packages from requirements.txt
echo First start may take several minutes. Please wait.
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto err_install

"%VENV_PY%" -c "import flask" >nul 2>&1
if errorlevel 1 goto err_flask

echo.
echo Starting server with venv Python.
echo.
start "" "%TVW_URL%"
"%VENV_PY%" app.py
echo.
echo Server stopped. Exit code %ERRORLEVEL%.
goto end_ok

:err_app
echo.
echo ERROR: app.py not found.
echo Current folder: "%CD%"
goto end_fail

:err_req
echo.
echo ERROR: requirements.txt not found.
echo Current folder: "%CD%"
goto end_fail

:err_python
echo.
echo ERROR: Python not found. Install Python 3.12 or 3.13 from python.org
echo and enable Add python.exe to PATH or use the py launcher.
goto end_fail

:err_venv
echo.
echo ERROR: Could not create venv.
goto end_fail

:err_pip
echo.
echo ERROR: pip upgrade failed.
goto end_fail

:err_install
echo.
echo ERROR: pip install failed. Check network.
goto end_fail

:err_flask
echo.
echo ERROR: Flask missing after install.
echo Run from this folder: venv\Scripts\python.exe -m pip install -r requirements.txt
goto end_fail

:end_fail
echo.
pause
endlocal
exit /b 1

:end_ok
pause
endlocal
exit /b 0
