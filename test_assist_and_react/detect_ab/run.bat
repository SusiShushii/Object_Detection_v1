@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem  old-vs-new test: alternates old / new / new_fast code each round and summarises detect speed + first timed click.
rem  usage: run.bat 2_4        (item name(s) or profile, same as assist; asks if you give none)
rem  options: --dry (no real clicks)   --modes old,new   Settings: ..\test_config.py   Stop: hold F12 or Ctrl+C
rem  results: output\test\detect_ab\<time>\ (test.log, round videos, summary.csv)

if not exist "..\..\.venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

set "ARGS=%*"
if "%~1"=="" (
    set /p "ARGS=Item name(s) or profile (e.g. 2_4): "
)
if not defined ARGS (
    echo Nothing entered.
    pause
    exit /b 1
)

"..\..\.venv\Scripts\python.exe" run_detect_ab.py !ARGS!
pause
