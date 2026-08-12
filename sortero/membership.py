"""Remember which playlists a track belonged to while it is away being analysed.

Staging a track for Platinum Notes / Mixed In Key moves it out of the folder it
was curated in, and it comes back renamed, in a different place. Without a
record of where it came from, that track would quietly fall out of the set or
vibe playlist it belonged to.

Matching has to survive the round trip, and the name is not stable: Platinum
Notes appends '_PN', and a track with no artist tag is staged as
'Unknown Artist - Title', which then reads back as a real artist. So each entry
carries two keys - artist+title, and title alone - and a claim tries the precise
one first.
"""
import json, os, re

from . import paths
from .dupes import ident, norm_title

PENDING = "pending-membership.json"
PLACEHOLDER = re.compile(r"(?i)^(unknown artist|unknown|various artists|va)$")


def _file():
    return os.path.join(paths.data_dir(), PENDING)


def _load():
    try:
        with open(_file()) as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(data):
    with open(_file(), "w") as fh:
        json.dump(data, fh, indent=1)


def _keys(rec):
    """(precise key, loose key). The loose key ignores the artist entirely."""
    artist = rec.artist or ""
    precise = ident(rec) if not PLACEHOLDER.match(artist.strip()) else None
    loose = norm_title(rec.title or "") or None
    return precise, loose


def remember(pairs):
    """pairs: iterable of (Rec, [playlist name, ...])."""
    data = _load()
    for rec, names in pairs:
        if not names:
            continue
        precise, loose = _keys(rec)
        found = None
        for e in data:
            if (precise and e.get("ident") == precise) or \
               (loose and e.get("title") == loose):
                found = e
                break
        if found is None:
            found = {"ident": precise, "title": loose,
                     "display": rec.display, "playlists": []}
            data.append(found)
        for n in names:
            if n not in found["playlists"]:
                found["playlists"].append(n)
    _save(data)
    return len(data)


def claim(rec):
    """Playlists this track owes a place in. Does not clear the entry."""
    precise, loose = _keys(rec)
    data = _load()
    if precise:
        for e in data:
            if e.get("ident") == precise:
                return e["playlists"]
    if loose:
        for e in data:
            if e.get("title") == loose:
                return e["playlists"]
    return []


def release(recs):
    """Forget the entries for these tracks, once they are back in playlists."""
    data = _load()
    drop = []
    for r in recs:
        precise, loose = _keys(r)
        for e in data:
            if (precise and e.get("ident") == precise) or \
               (loose and e.get("title") == loose):
                drop.append(id(e))
    _save([e for e in data if id(e) not in drop])


def pending_count():
    return len(_load())


def pending():
    return _load()
