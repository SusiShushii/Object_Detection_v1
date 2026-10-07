@echo off
setlocal
cd /d "%~dp0"

rem ===========================================================================
rem  run.bat - run main.py with the .venv Python
rem    run.bat skybug_spike skybug_tail     (priority = order of names)
rem    run.bat carrot --dry                 (no real clicks, log only)
rem  Stop: hold ESC or press Ctrl+C
rem ===========================================================================

if not exist ".venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py %*
pause
