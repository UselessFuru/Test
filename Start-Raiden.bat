@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto dependencies
echo Preparing Raiden Companion for the first run...
set "RAIDEN_PYTHON="
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 set "RAIDEN_PYTHON=py -3.12"
if defined RAIDEN_PYTHON goto create_env
py -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 set "RAIDEN_PYTHON=py -3.11"
if defined RAIDEN_PYTHON goto create_env
py -3.13 -c "import sys" >nul 2>nul
if not errorlevel 1 set "RAIDEN_PYTHON=py -3.13"
if defined RAIDEN_PYTHON goto create_env
python -c "import sys; assert (3,10) <= sys.version_info[:2] <= (3,13)" >nul 2>nul
if not errorlevel 1 set "RAIDEN_PYTHON=python"
if defined RAIDEN_PYTHON goto create_env
echo Install 64-bit Python 3.12 from python.org, then run this file again.
echo The Python launcher or Add Python to PATH option should be enabled.
pause
exit /b 1
:create_env
%RAIDEN_PYTHON% -m venv .venv
if errorlevel 1 goto failure
:dependencies
".venv\Scripts\python.exe" -c "import PySide6, matplotlib, lxml.etree, trafilatura; assert PySide6.__version__ == '6.8.3'; assert matplotlib.__version__ == '3.10.8'; assert lxml.__version__ == '6.1.3'; assert trafilatura.__version__ == '2.3.1'" >nul 2>nul
if not errorlevel 1 goto launch
echo Installing interface, equation rendering, and web lookup. This update needs internet access once.
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failure
:launch
if "%~1" == "--check-connections" goto check_connections
start "" ".venv\Scripts\pythonw.exe" "app.py" %*
exit /b 0
:check_connections
".venv\Scripts\python.exe" check_connections.py
pause
exit /b
:failure
echo Setup failed. Check the message above and open START-HERE.html for help.
pause
exit /b 1
