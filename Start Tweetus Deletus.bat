@echo off
rem Double-click to open Tweetus Deletus (Windows).
rem The first run sets things up, which takes about a minute.
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY goto :nopython
%PY% -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul || goto :nopython

if not exist ".venv\.installed" (
  echo First run: setting up, about a minute...
  %PY% -m venv .venv || goto :failed
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :failed
  echo ok> ".venv\.installed"
)

".venv\Scripts\python.exe" -m tweetus_deletus %*
pause
exit /b 0

:nopython
echo Tweetus Deletus needs Python 3.11 or newer.
echo Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo then open this file again.
pause
exit /b 1

:failed
echo Couldn't set up Tweetus Deletus. Check your internet connection and try again.
pause
exit /b 1
