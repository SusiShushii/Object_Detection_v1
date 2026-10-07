@echo off
setlocal
cd /d "%~dp0"

rem ===========================================================================
rem  install.bat - set up Python + dependencies for Object_Detection
rem  Python version matches the original dev environment: 3.14 (3.14.7)
rem  Creates a virtual environment in .venv and installs requirements.txt
rem ===========================================================================

set PY_VER=3.14
set PY_FULL=3.14.7

echo.
echo === Object_Detection: install ===
echo.

rem ---- 1) Find Python %PY_VER% (via the "py" launcher) ----------------------
py -%PY_VER% --version >nul 2>&1
if not errorlevel 1 goto :have_python

echo Python %PY_VER% was not found.
echo.

rem The Python install manager (PyManager) supports "py install <version>"
where py >nul 2>&1
if errorlevel 1 goto :no_python
choice /C YN /M "Install Python %PY_FULL% now with 'py install'"
if errorlevel 2 goto :no_python
py install %PY_FULL%
py -%PY_VER% --version >nul 2>&1
if not errorlevel 1 goto :have_python

:no_python
echo.
echo Please install Python %PY_FULL% (64-bit) from:
echo   https://www.python.org/downloads/
echo then run install.bat again.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:have_python
for /f "delims=" %%v in ('py -%PY_VER% --version') do echo Found %%v

rem ---- 2) Create virtual environment ---------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo Virtual environment .venv already exists - reusing it.
) else (
    echo Creating virtual environment in .venv ...
    py -%PY_VER% -m venv .venv
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

rem ---- 3) Install dependencies ---------------------------------------------
echo.
echo Installing dependencies from requirements.txt ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install dependencies.
    pause
    exit /b 1
)

rem ---- 4) Check ------------------------------------------------------------
echo.
".venv\Scripts\python.exe" -c "import sys, cv2, numpy, mss; print('Python', sys.version.split()[0], '| opencv', cv2.__version__, '| numpy', numpy.__version__, '| mss', mss.__version__)"
if errorlevel 1 (
    echo Check failed.
    pause
    exit /b 1
)

echo.
echo === Done ===
echo Run:  run.bat skybug_spike skybug_tail
echo       run.bat carrot --dry
echo.
pause
