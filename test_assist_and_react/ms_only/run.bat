@echo off
setlocal
cd /d "%~dp0"

rem  ms-only test: clicks only at CLICK_AT_MS (test_config.py) where the mouse is. No colour / GET! reaction.
rem  options: --dry (no real clicks)   stop: hold F12 or Ctrl+C

if not exist "..\..\.venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

"..\..\.venv\Scripts\python.exe" run_timed_only.py %*
pause
