@echo off
setlocal
cd /d "%~dp0"

rem ===========================================================================
rem  venv_cmd.bat - open a Command Prompt with the .venv already activated
rem  then you can type:
rem      python main.py water_dungeon
rem      python main.py carrot --dry
rem  shortcuts (only in this window):
rem      main water_dungeon       = python main.py water_dungeon
rem      crop                     = python crop_templates.py
rem  Stop the bot: hold F12 or press Ctrl+C
rem ===========================================================================

if not exist ".venv\Scripts\activate.bat" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

cmd /k ".venv\Scripts\activate.bat & doskey main=python main.py $* & doskey crop=python crop_templates.py $* & echo. & echo [.venv ready]  python main.py water_dungeon   or   main water_dungeon & echo."
