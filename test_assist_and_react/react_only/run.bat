@echo off
setlocal
cd /d "%~dp0"
set TEST_NAME=react_only

rem  react-only test (legacy): clicks by colour only (dim -> ready / GET!), with its backup clicks. Settings: ..\test_config.py
rem  stop: hold F12 or Ctrl+C

if not exist "..\..\.venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

"..\..\.venv\Scripts\python.exe" ..\run_test.py assist --react --record %*
pause
