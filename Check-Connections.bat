@echo off
setlocal
cd /d "%~dp0"
call Start-Raiden.bat --check-connections
