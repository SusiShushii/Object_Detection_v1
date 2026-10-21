@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)
rem  usage: run_random.bat blue_seaweed carrot   (priority = order; add --dry for no real clicks)
".venv\Scripts\python.exe" main.py random %*
pause
