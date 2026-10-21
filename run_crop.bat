@echo off
setlocal
cd /d "%~dp0"

rem  run_crop.bat - auto-crop item images from saved loot screenshots (output\loot) -> output\crops
rem  then rename new_XXX.png to the item name and move it into assets	emplates

if not exist ".venv\Scripts\python.exe" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py crop %*
pause
