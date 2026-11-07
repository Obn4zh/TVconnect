# PyInstaller spec — one-folder build for Windows and Linux.
#
# adb (Android platform-tools) is bundled into _internal/adb when found on the build machine, so the
# client works on a PC without the Android SDK. Override the source folder with TVCONNECT_ADB_DIR.
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

sys.path.insert(0, os.path.abspath(".."))
from tvconnect_desktop.adb.adb_client import find_adb  # noqa: E402

av_datas, av_binaries, av_hidden = collect_all("av")  # PyAV: compiled submodules + bundled FFmpeg libs

adb_files = []
adb_dir = os.environ.get("TVCONNECT_ADB_DIR") or (str(Path(find_adb()).parent) if find_adb() else "")
if adb_dir:
    names = ["adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"] if sys.platform == "win32" else ["adb"]
    for name in names + ["NOTICE.txt", "source.properties"]:
        p = Path(adb_dir) / name
        if p.is_file():
            adb_files.append((str(p), "adb"))
    print(f"[tvconnect.spec] bundling adb from {adb_dir}: {[Path(f).name for f, _ in adb_files]}")
else:
    print("[tvconnect.spec] WARNING: adb not found, the build will rely on adb installed on the target PC")

a = Analysis(
    ["entry.py"],
    pathex=[".."],
    binaries=av_binaries,
    datas=av_datas + adb_files,
    hiddenimports=av_hidden + collect_submodules("zeroconf") + collect_submodules("keyring.backends"),
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TVconnect", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="TVconnect")
