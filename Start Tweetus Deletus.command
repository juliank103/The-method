#!/bin/bash
# Double-click to open Tweetus Deletus (macOS; on Linux run ./"Start Tweetus Deletus.command").
# The first run sets things up, which takes about a minute.
cd "$(dirname "$0")" || exit 1

pause_then_exit() {
  read -r -p "Press Enter to close this window." _
  exit 1
}

PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Tweetus Deletus needs Python 3.11 or newer."
  echo "Get it from https://www.python.org/downloads/ and then open this file again."
  pause_then_exit
fi

if [ ! -f ".venv/.installed" ]; then
  echo "First run: setting up (about a minute)..."
  "$PY" -m venv .venv || { echo "Couldn't create a Python environment."; pause_then_exit; }
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt \
    || { echo "Couldn't install what Tweetus Deletus needs. Check your internet connection."; pause_then_exit; }
  touch .venv/.installed
fi

exec .venv/bin/python -m tweetus_deletus "$@"
