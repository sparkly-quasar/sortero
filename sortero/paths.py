"""Platform-appropriate locations for config and data.

macOS   ~/Library/Application Support/Sortero
Windows %APPDATA%\\Sortero
Linux   $XDG_CONFIG_HOME/sortero (config), $XDG_DATA_HOME/sortero (data)
"""
import os, sys, subprocess

APP_NAME = "Sortero"
IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"
IS_LINUX = not IS_MAC and not IS_WIN


def _base(kind):
    if IS_MAC:
        return os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
    if IS_WIN:
        root = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        return os.path.join(root, APP_NAME)
    env = "XDG_DATA_HOME" if kind == "data" else "XDG_CONFIG_HOME"
    default = "~/.local/share" if kind == "data" else "~/.config"
    return os.path.join(os.path.expanduser(os.environ.get(env) or default),
                        APP_NAME.lower())


def config_dir():
    d = _base("config")
    os.makedirs(d, exist_ok=True)
    return d


def data_dir():
    d = _base("data")
    os.makedirs(d, exist_ok=True)
    return d


def journals_dir():
    d = os.path.join(data_dir(), "journals")
    os.makedirs(d, exist_ok=True)
    return d


def prefs_file():
    return os.path.join(config_dir(), "prefs.txt")


def reveal(path):
    """Show a file in the system file manager."""
    try:
        if IS_MAC:
            subprocess.run(["open", "-R", path], check=False)
        elif IS_WIN:
            subprocess.run(["explorer", "/select,", os.path.normpath(path)], check=False)
        else:
            subprocess.run(["xdg-open", os.path.dirname(path) or "."], check=False)
    except Exception:
        pass
