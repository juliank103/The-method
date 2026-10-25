"""Entry point for the double-click app (built with PyInstaller).

Opening the app with no arguments starts Tweetus Deletus and opens it in
the browser, exactly like running `tweetus-deletus` from a terminal.
"""

import os
import sys


def _give_output_somewhere():
    # A windowed build has no console, so print() would fail. Log to a file.
    if sys.stdout is not None and sys.stderr is not None:
        return
    folder = os.path.join(os.path.expanduser("~"), ".config", "tweetus_deletus")
    os.makedirs(folder, exist_ok=True)
    log = open(os.path.join(folder, "app.log"), "a", buffering=1,
               encoding="utf-8")
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def main():
    _give_output_somewhere()
    # Older macOS versions pass a -psn_... argument when opened from Finder.
    sys.argv = [a for a in sys.argv if not a.startswith("-psn_")]
    from tweetus_deletus.cli import main as cli_main
    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
