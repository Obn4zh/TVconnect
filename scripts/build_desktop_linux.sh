#!/usr/bin/env bash
# Builds the Linux desktop client and packs it for transfer:
#   desktop/dist/TVconnect/TVconnect            (one-folder build, adb bundled in _internal/adb)
#   desktop/dist/TVconnect-linux-x86_64.tar.gz  (copy to the other PC: tar xzf ..., run TVconnect/TVconnect)
#
# Works without root: if python3-venv is missing, pip is bootstrapped with get-pip.py.
# adb: taken from TVCONNECT_ADB_DIR, else from an installed adb, else Android platform-tools are downloaded.
# Build on the OLDEST distro you want to support (the binary needs glibc >= the build machine's).
set -euo pipefail
cd "$(dirname "$0")/../desktop"
PY=${PYTHON:-python3}

if [ ! -x .venv/bin/python ]; then
    if ! "$PY" -m venv .venv 2>/dev/null; then
        echo "python3-venv not installed, bootstrapping pip without root"
        rm -rf .venv
        "$PY" -m venv --without-pip .venv
        curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
        .venv/bin/python /tmp/get-pip.py -q
    fi
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt pyinstaller pytest

if [ -z "${TVCONNECT_ADB_DIR:-}" ]; then
    if command -v adb >/dev/null; then
        TVCONNECT_ADB_DIR=$(dirname "$(readlink -f "$(command -v adb)")")
    else
        echo "Downloading Android platform-tools (adb) to bundle"
        mkdir -p build/platform-tools-dl
        curl -fsSL https://dl.google.com/android/repository/platform-tools-latest-linux.zip -o build/platform-tools.zip
        .venv/bin/python -c "import zipfile; zipfile.ZipFile('build/platform-tools.zip').extractall('build/platform-tools-dl')"
        chmod +x build/platform-tools-dl/platform-tools/adb
        TVCONNECT_ADB_DIR=$PWD/build/platform-tools-dl/platform-tools
    fi
fi
export TVCONNECT_ADB_DIR

.venv/bin/python -m pytest -q
.venv/bin/python -m PyInstaller --noconfirm --log-level WARN packaging/tvconnect.spec
cp packaging/tvconnect.desktop dist/TVconnect/
tar -C dist -czf dist/TVconnect-linux-x86_64.tar.gz TVconnect
echo "Done: desktop/dist/TVconnect-linux-x86_64.tar.gz ($(du -h dist/TVconnect-linux-x86_64.tar.gz | cut -f1))"
