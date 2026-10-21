@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem ===========================================================================
rem  run_random.bat - random loot mode (waits for the ? boxes to vanish, then moves the mouse and clicks)
rem  first run asks which item(s) or profile you want; it is remembered in output\random_target.txt
rem  next runs: press Enter to reuse it, or type new names (e.g. blue_seaweed carrot  or  example_dungeon)
rem  add --dry as an argument for no real clicks.   Stop: hold F12 or Ctrl+C
rem ===========================================================================

if not exist ".venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

set "SAVE=output\random_target.txt"
set "TARGET="
if exist "%SAVE%" set /p TARGET=<"%SAVE%"

echo.
".venv\Scripts\python.exe" main.py random --list
echo.
if defined TARGET (
    echo Saved target: !TARGET!
    set "NEW="
    set /p "NEW=Press Enter to use it, or type new item(s)/profile: "
    if defined NEW set "TARGET=!NEW!"
) else (
    set /p "TARGET=Which item(s) or profile do you want? (e.g. blue_seaweed carrot  or  example_dungeon): "
)

if not defined TARGET (
    echo Nothing entered.
    pause
    exit /b 1
)

if not exist "output" mkdir "output"
>"%SAVE%" echo !TARGET!

echo.
echo Running: main.py random !TARGET! %*
".venv\Scripts\python.exe" main.py random !TARGET! %*
pause
