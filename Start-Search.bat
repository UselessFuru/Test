@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" search_setup.py
) else (
  py -3 search_setup.py
)
pause
