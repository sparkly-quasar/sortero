"""Small JSON settings store, with migration from the original prefs.txt."""
import json, os

from . import paths

FILE = "settings.json"
DEFAULTS = {
    "root": "",
    "setup_complete": False,
    "check_updates_on_launch": True,
    "last_update_check": 0,
    "github_token": "",        # only needed while the repo is private
}


def _file():
    return os.path.join(paths.config_dir(), FILE)


def load():
    data = dict(DEFAULTS)
    try:
        with open(_file()) as fh:
            data.update(json.load(fh))
    except (OSError, json.JSONDecodeError):
        # migrate the old single-line prefs.txt
        try:
            with open(paths.prefs_file()) as fh:
                old = fh.read().strip()
            if old:
                data["root"] = old
                data["setup_complete"] = True      # existing users skip the wizard
                save(data)
        except OSError:
            pass
    return data


def save(data):
    with open(_file(), "w") as fh:
        json.dump(data, fh, indent=1)


def get(key, default=None):
    return load().get(key, DEFAULTS.get(key, default))


def set(key, value):
    data = load()
    data[key] = value
    save(data)
    return data
