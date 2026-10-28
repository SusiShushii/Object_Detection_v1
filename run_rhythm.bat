@echo off
setlocal
cd /d "%~dp0"

rem  rhythm = Dance Audition minigame: bot presses A / S / D with the notes
rem  run.bat-style options:  run_rhythm.bat calibrate  (check positions)   run_rhythm.bat --dry  (no key presses, log only)
rem  settings are at the top of rhythmot.py   stop: hold F12 or Ctrl+C

if not exist ".venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py rhythm %*
pause
