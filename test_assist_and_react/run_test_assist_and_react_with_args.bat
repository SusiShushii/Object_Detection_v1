@echo off
setlocal
cd /d "%~dp0"
if not exist "..\.venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)
"..\.venv\Scripts\python.exe" run_test.py assist 3979 --react --record --wgc %*
pause
