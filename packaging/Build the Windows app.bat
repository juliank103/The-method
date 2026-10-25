@echo off
rem Double-click on Windows to build "Tweetus Deletus.exe" (takes a couple of minutes).
rem Needs Python 3.11 or newer from https://www.python.org/downloads/
cd /d "%~dp0.."

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY goto :nopython
%PY% -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul || goto :nopython

set "CLIENT_ID="
set /p "CLIENT_ID=Paste your X app's Client ID to build it in (or just press Enter to skip): "

echo Building Tweetus Deletus, a couple of minutes...
%PY% -m venv .build-venv || goto :failed
".build-venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".build-venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt pyinstaller==6.11.1 pillow || goto :failed

copy /y "tweetus_deletus\bundled.json" "tweetus_deletus\bundled.json.bak" >nul
if defined CLIENT_ID ".build-venv\Scripts\python.exe" -c "import json, os; json.dump({'client_id': os.environ['CLIENT_ID'].strip()}, open(r'tweetus_deletus\bundled.json', 'w'))"
".build-venv\Scripts\pyinstaller.exe" --noconfirm --log-level WARN packaging\tweetus.spec
set "STATUS=%ERRORLEVEL%"
move /y "tweetus_deletus\bundled.json.bak" "tweetus_deletus\bundled.json" >nul
if not "%STATUS%"=="0" goto :failed

echo.
echo Done! The app is dist\Tweetus Deletus.exe - send that one file to whoever needs it.
explorer /select,"dist\Tweetus Deletus.exe"
pause
exit /b 0

:nopython
echo Install Python 3.11 or newer from https://www.python.org/downloads/ first
echo (tick "Add python.exe to PATH"), then open this file again.
pause
exit /b 1

:failed
echo The build failed. Check your internet connection and the messages above.
pause
exit /b 1
