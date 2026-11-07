"""Per-user data directories (Windows: %APPDATA%\\TVconnect, Linux: $XDG_CONFIG_HOME/tvconnect)."""

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("TVCONNECT_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "TVconnect"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "TVconnect"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tvconnect"
    base.mkdir(parents=True, exist_ok=True)
    return base


def log_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
