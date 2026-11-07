@echo off
setlocal
cd /d "%~dp0"
set TEST_NAME=assist_and_react

rem  timed click (3979 ms) + colour react, time given on the command line (legacy). Settings: ..\test_config.py
rem  stop: hold F12 or Ctrl+C

if not exist "..\..\.venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

"..\..\.venv\Scripts\python.exe" ..\run_test.py assist 3979 --react --record %*
pause
