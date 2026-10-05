"""Where things live. Settings roam (APPDATA); models and the private Ollama runtime are big, so they go to
LOCALAPPDATA on Windows and ~/.local/share elsewhere."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True inside the PyInstaller-built .exe."""
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    d = Path(os.environ.get("JPENSUB_DATA") or base / "JPENSubMaker")
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    d = data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ollama_dir() -> Path:
    return data_dir() / "ollama"


def logs_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
