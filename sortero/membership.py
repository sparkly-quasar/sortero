"""Remember which playlists a track belonged to while it is away being analysed.

Staging a track for analysis moves it out of the folder it
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


# A track staged out of Tracks/<Genre> came from a genre folder, not a curated
# playlist. Remember the genre instead - otherwise it returns with no genre tag
# (Platinum Notes drops it) and lands in Unsorted.
GENRE_FOLDER = re.compile(r"^Tracks\s*-\s*(.+)$")


def remember(pairs):
    """pairs: iterable of (Rec, [playlist name, ...], genre) or (Rec, names)."""
    data = _load()
    for item in pairs:
        rec, names = item[0], item[1]
        genre = item[2] if len(item) > 2 else None
        if not names and not genre:
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
                     "display": rec.display, "playlists": [], "genre": None}
            data.append(found)
        if genre and not found.get("genre"):
            found["genre"] = genre
        for n in names or ():
            if n not in found["playlists"]:
                found["playlists"].append(n)
    _save(data)
    return len(data)


def _find(rec):
    precise, loose = _keys(rec)
    data = _load()
    if precise:
        for e in data:
            if e.get("ident") == precise:
                return e
    if loose:
        for e in data:
            if e.get("title") == loose:
                return e
    return None


def claim(rec):
    """Curated playlists this track owes a place in.

    Genre folders under Tracks/ are excluded - re-adding those would build
    playlists that just mirror the folder tree.
    """
    e = _find(rec)
    if not e:
        return []
    return [n for n in e.get("playlists", []) if not GENRE_FOLDER.match(n)]


def claim_genre(rec):
    """The genre this track had before it was staged, if known."""
    e = _find(rec)
    if not e:
        return None
    if e.get("genre"):
        return e["genre"]
    # Entries written before genre was recorded still encode it in the name.
    for n in e.get("playlists", []):
        m = GENRE_FOLDER.match(n)
        if m:
            g = m.group(1).strip()
            if g and g.lower() != "unsorted":
                return g
    return None


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
