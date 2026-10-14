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
    # how "A - B" filenames are read: "artist-title" or "title-artist"
    "name_order": "artist-title",
    # filenames (lowercased) of tracks never flagged as needing a genre
    "genre_ignored": [],
    # Gig prep: which players to prepare for, what files that won't play there
    # become, and where those copies go ("" = next to the collection)
    "gig_profile": "pioneer-denon",
    "gig_target": "mp3",
    "gig_library": "",
    # playlists Gig prep reads from outside the collection's _Playlists folder:
    # [{"kind", "path", "name", "ref"}] (see gig/sources.py)
    "gig_sources": [],
    # where Shrink to MP3 moves the originals ("" = "<collection> - Originals")
    "shrink_archive": "",
    # a rekordbox collection XML to take cue points from ("" = none)
    "gig_rekordbox_xml": "",
    # move cues on MP3 copies 25 ms later, for a rekordbox that ignores the
    # LAME gapless header (see gig/rbxml.py)
    "gig_mp3_cue_shift": False,
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
