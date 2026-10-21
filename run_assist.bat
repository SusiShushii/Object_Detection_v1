@echo off
setlocal
cd /d "%~dp0"

rem  assist = you control the mouse; bot clicks at ASSIST_CLICK_AT_MS and when the slot turns normal / GET!
rem  settings (time, record, wgc/mss) are in config.py   stop: hold F12 or Ctrl+C

if not exist ".venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py assist %*
pause
