@echo off
setlocal
rem  go to the project root (parent of this folder) so paths / output\ work like normal runs
cd /d "%~dp0.."

rem ===========================================================================
rem  venv_cmd.bat (test folder) - open a Command Prompt with the .venv already activated
rem  then you can type:
rem      python test_assist_and_react\ms_only\run_timed_only.py          (timed clicks only, CLICK_AT_MS in test_config.py)
rem      python test_assist_and_react\ms_only\run_timed_only.py --dry    (no real clicks)
rem  shortcuts (only in this window):
rem      ms            = ms-only test        ms --dry = no real clicks
rem      dab 2_4       = old-vs-new detect/ms test (profile or items)
rem      legacy ...    = python test_assist_and_react\run_test.py ...   (react_only / ab / assist_and_react modes)
rem  Test settings: test_assist_and_react\test_config.py     Stop: hold F12 or press Ctrl+C
rem ===========================================================================

if not exist ".venv\Scripts\activate.bat" (
    echo .venv not found - run install.bat first.
    pause
    exit /b 1
)

cmd /k ".venv\Scripts\activate.bat & doskey ms=python test_assist_and_react\ms_only\run_timed_only.py $* & doskey dab=python test_assist_and_react\detect_ab\run_detect_ab.py $* & doskey legacy=python test_assist_and_react\run_test.py $* & echo. & echo [.venv ready]  type:  ms   or   ms --dry   (settings: test_assist_and_react\test_config.py) & echo."
