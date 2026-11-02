@echo off
setlocal
cd /d "%~dp0"
title Arena Desktop
set "PYTHONUTF8=1"

if exist ".venv\Scripts\python.exe" goto run
where py >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=py -3"
) else (
  where python >nul 2>&1
  if errorlevel 1 goto no_python
  set "PY_CMD=python"
)

echo First launch: creating a virtual environment and installing dependencies.
echo This needs internet access. Python 3.11+ and Edge WebView2 are required.
pause
%PY_CMD% -c "import sys; sys.exit(not sys.version_info.__ge__((3,11)))"
if errorlevel 1 goto old_python
%PY_CMD% -m venv .venv
if errorlevel 1 goto setup_error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto setup_error

:run
".venv\Scripts\python.exe" -m arena.desktop
if errorlevel 1 (
  echo.
  echo Application exited with an error. Check the message above and desktop.log.
  pause
)
exit /b

:no_python
echo Python 3.11 or newer was not found. Install it from python.org and try again.
pause
exit /b 1
:old_python
echo Python 3.11 or newer is required.
pause
exit /b 1
:setup_error
echo Setup failed. Check your internet connection and the error above.
echo You can delete the incomplete .venv folder and run this launcher again.
pause
exit /b 1
