#!/bin/bash
# Double-click on a Mac to build "Tweetus Deletus.app" (takes a couple of minutes).
# Needs Python 3.11 or newer from https://www.python.org/downloads/
cd "$(dirname "$0")/.." || exit 1

pause_then_exit() {
  read -r -p "Press Enter to close this window." _
  exit "${1:-1}"
}

if [ "$(uname)" != "Darwin" ]; then
  echo "This builds the Mac app, so run it on a Mac."
  pause_then_exit
fi

PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Install Python 3.11 or newer from https://www.python.org/downloads/ first."
  pause_then_exit
fi

echo "Paste your X app's Client ID to build it in (or just press Enter to skip):"
read -r CLIENT_ID

echo "Building Tweetus Deletus (a couple of minutes)..."
"$PY" -m venv .build-venv || { echo "Couldn't create a Python environment."; pause_then_exit; }
.build-venv/bin/python -m pip install --quiet --upgrade pip
.build-venv/bin/python -m pip install --quiet -r requirements.txt pyinstaller==6.11.1 pillow \
  || { echo "Couldn't download the build tools. Check your internet connection."; pause_then_exit; }

BUNDLED=tweetus_deletus/bundled.json
cp "$BUNDLED" "$BUNDLED.bak"
if [ -n "$CLIENT_ID" ]; then
  CLIENT_ID="$CLIENT_ID" .build-venv/bin/python -c \
    "import json, os; json.dump({'client_id': os.environ['CLIENT_ID'].strip()}, open('$BUNDLED', 'w'))"
fi
.build-venv/bin/pyinstaller --noconfirm --log-level WARN packaging/tweetus.spec
status=$?
mv "$BUNDLED.bak" "$BUNDLED"
if [ $status -ne 0 ]; then
  echo "The build failed (see the messages above)."
  pause_then_exit
fi

rm -f "dist/Tweetus-Deletus-Mac.zip"
ditto -c -k --keepParent "dist/Tweetus Deletus.app" "dist/Tweetus-Deletus-Mac.zip"
echo
echo "Done! In the dist folder you'll find:"
echo "  Tweetus Deletus.app        - the app (drag it to Applications)"
echo "  Tweetus-Deletus-Mac.zip    - the same app, zipped, to send to someone"
open -R "dist/Tweetus Deletus.app"
pause_then_exit 0
