@echo off
setlocal
cd /d "%~dp0"
set TEST_NAME=ab

rem  A/B test (legacy): alternates timed-only and react each round and summarises which collects faster. Settings: ..\test_config.py
rem  stop: hold F12 or Ctrl+C

if not exist "..\..\.venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

"..\..\.venv\Scripts\python.exe" ..\run_test.py assist 3979 --react --ab %*
pause
