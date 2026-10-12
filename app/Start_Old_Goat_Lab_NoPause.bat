@echo off
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "TVW_ENABLE_LAB_MODULES=1"
set "TVW_URL=http://127.0.0.1:5000/recipes"
cd /d "%~dp0"

echo The Vape Workshop Calculator
echo Lab bridge TVW_ENABLE_LAB_MODULES=%TVW_ENABLE_LAB_MODULES%
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
echo WARNING: Python 3.14 or newer. Prefer 3.12 or 3.13.
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
echo WARNING: py -3 is 3.14 or newer. Prefer py -3.12.
set "PYCREATE=py -3"
goto pick_py_done
:pick_py_done

if exist "%VENV_PY%" goto have_venv
echo Creating venv...
%PYCREATE% -m venv venv
if errorlevel 1 exit /b 1
:have_venv

"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
"%VENV_PY%" -c "import flask" >nul 2>&1
if errorlevel 1 goto err_flask

start "" "%TVW_URL%"
"%VENV_PY%" app.py
set "OG_EXIT=%ERRORLEVEL%"
if "%OG_EXIT%"=="0" goto end_clean
echo Server exited with code %OG_EXIT%.
pause
exit /b %OG_EXIT%

:end_clean
exit /b 0

:err_app
echo ERROR: app.py not found. Folder "%CD%"
exit /b 1

:err_req
echo ERROR: requirements.txt not found.
exit /b 1

:err_python
echo ERROR: Python not on PATH.
exit /b 1

:err_flask
echo ERROR: Flask missing. Run venv\Scripts\python.exe -m pip install -r requirements.txt
exit /b 1
