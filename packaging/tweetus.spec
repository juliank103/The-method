# PyInstaller recipe for the double-click app.
#   pyinstaller --noconfirm packaging/tweetus.spec
# macOS: dist/Tweetus Deletus.app    Windows/Linux: dist/Tweetus Deletus(.exe)
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
PKG = os.path.join(ROOT, "tweetus_deletus")
ICON = os.path.join(SPECPATH, "icon.png")  # converted to .icns/.ico via Pillow
NAME = "Tweetus Deletus"
with open(os.path.join(PKG, "__init__.py"), encoding="utf-8") as fh:
    VERSION = re.search(r'__version__ = "([^"]+)"', fh.read()).group(1)

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    datas=[
        (os.path.join(PKG, "ui"), "tweetus_deletus/ui"),
        (os.path.join(PKG, "bundled.json"), "tweetus_deletus"),
    ],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME,
              console=False, icon=ICON)
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=ICON,
        bundle_identifier="app.tweetusdeletus",
        info_plist={
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            # No Dock icon: the app lives in the browser and quits from there.
            "LSUIElement": True,
        },
    )
else:
    # One file to hand over. No console window on Windows.
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=NAME,
              console=False, icon=ICON, upx=False)
