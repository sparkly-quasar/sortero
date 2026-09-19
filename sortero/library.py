"""Scan a DJ collection into track records and compute library health."""
import os, re, collections
from dataclasses import dataclass, field
from . import settings
from .common import (AUDIO_EXTS, ARTIST_TITLE, TITLE_ARTIST, is_spam, clean_stem, fold,
                     halves, split_artist_title, to_camelot)
from .tagio import Track

# Folders Sortero never touches: the user's analysis staging lanes, plus
# Sortero's own quarantine.
PROTECTED = {"To Be Processed", "Processed", "_Quarantine", "_Playlists"}

# Folders holding your own recordings rather than tracks to play. Nothing in
# here ever needs key/BPM analysis.
RECORDING_DIRS = {"Recorded Mixes", "Mixes", "Recordings"}


@dataclass
class Rec:
    path: str
    rel: str
    size: int = 0
    ext: str = ""
    artist: str = None
    title: str = None
    genre: str = None
    key: str = None
    bpm: str = None
    grouping: str = None
    comment: str = None
    energylevel: str = None
    album: str = None
    duration: float = 0.0
    bitrate: int = 0
    protected: bool = False
    # True when the tag was missing and the name had to be read off the
    # filename. Only tagged names are evidence of how filenames are ordered.
    artist_from_name: bool = False
    title_from_name: bool = False

    @property
    def top(self):
        return self.rel.split(os.sep)[0] if os.sep in self.rel else "(root)"

    @property
    def camelot(self):
        return to_camelot(self.key) or (self.key if _is_camelot(self.key) else None)

    @property
    def energy(self):
        # Mixed In Key's own ENERGYLEVEL tag is the most reliable source.
        if self.energylevel and str(self.energylevel).strip().isdigit():
            return int(str(self.energylevel).strip())
        for src in (self.grouping, self.comment):
            if src:
                m = re.search(r"(?i)energy\s*(\d+)", src)
                if m:
                    return int(m.group(1))
        return None

    @property
    def is_recording(self):
        """A recording of a set, not a track to mix with."""
        return any(p in RECORDING_DIRS for p in self.rel.split(os.sep))

    @property
    def analyzed(self):
        """Has this been through an analysis tool?

        Key only. Requiring BPM as well was wrong: Mixed In Key writes key and
        energy but frequently no BPM tag at all (260 of 261 vs 22 of 261 on a
        real processed batch), so demanding both sent fully-analysed tracks
        straight back to 'To Be Processed' - an endless loop. Key is the thing
        only an analysis tool provides; every DJ app derives BPM on import.
        """
        return bool(self.key)

    @property
    def display(self):
        a = self.artist or "[unknown]"
        t = self.title or clean_stem(self.path)
        return f"{a} - {t}"


def _is_camelot(v):
    return bool(v and re.fullmatch(r"(1[0-2]|[1-9])[AB]", v.strip(), re.I))


def is_protected(rel):
    parts = rel.split(os.sep)
    return any(p in PROTECTED for p in parts)


def scan(root, progress=None, order=None):
    """Walk root and read tags. Returns list[Rec].

    `order` says how to read a "A - B" filename when a track has no artist or
    title tag of its own; it defaults to the collection's saved setting.
    """
    order = order or settings.get("name_order") or ARTIST_TITLE
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("._"):
                continue
            if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                paths.append(os.path.join(dirpath, fn))
    paths.sort()

    recs = []
    total = len(paths)
    for i, p in enumerate(paths):
        if progress and i % 25 == 0:
            progress(i, total)
        rel = os.path.relpath(p, root)
        r = Rec(path=p, rel=rel, ext=os.path.splitext(p)[1].lower(),
                protected=is_protected(rel))
        try:
            r.size = os.path.getsize(p)
        except OSError:
            pass
        t = Track(p)
        if t.ok:
            r.artist = _clean(t.get("artist"))
            r.title = _clean(t.get("title"))
            r.genre = _clean(t.get("genre"))
            r.key = _clean(t.get("key"))
            r.bpm = _clean(t.get("bpm"))
            r.grouping = _clean(t.get("grouping"))
            r.comment = _clean(t.get("comment"))
            r.energylevel = _clean(t.get("energylevel"))
            r.album = _clean(t.get("album"))
            r.duration = t.length or 0.0
            r.bitrate = t.bitrate or 0
        if not r.artist or not r.title:
            a, ti = split_artist_title(clean_stem(p), order)
            if not r.artist and a:
                r.artist, r.artist_from_name = a, True
            if not r.title and ti:
                r.title, r.title_from_name = ti, True
        recs.append(r)
    if progress:
        progress(total, total)
    return recs


def _clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


# --- which way round are the filenames? --------------------------------------
def known_artists(recs):
    """Artists the tags themselves name, mapped to the files that name them.

    Names filled in from a filename are left out on purpose: they only repeat
    whatever order we already assumed, so counting them would prove nothing.
    """
    out = collections.defaultdict(set)
    for r in recs:
        if r.artist and not r.artist_from_name:
            out[fold(r.artist)].add(r.path)
    return out


def _vote(stem, path, known):
    """Which half of this stem is an artist another file's tags vouch for?

    Returns ARTIST_TITLE, TITLE_ARTIST, or None when neither half (or both)
    is a known artist. A file never votes on the strength of its own tags.
    """
    left, right = halves(stem)
    if not left:
        return None
    left_is = bool(known.get(fold(left), set()) - {path})
    right_is = bool(known.get(fold(right), set()) - {path})
    if left_is == right_is:
        return None
    return ARTIST_TITLE if left_is else TITLE_ARTIST


def _settled(a, b):
    """One stray match shouldn't re-tag anybody's library: want a real majority."""
    winner, loser = max(a, b), min(a, b)
    return winner >= 8 and winner >= 3 * max(loser, 1)


def name_order_votes(recs):
    """How this collection's filenames are ordered, where the tags can say.

    For every two-part filename, ask whether the left or the right half is an
    artist that some *other* file's tags name. That settles it for a collection
    whose tags are mostly right, and it stays quiet for one whose tags are
    missing or backwards throughout: there is nothing to cross-reference, and
    nothing about the words themselves says which is an artist and which is a
    title. That case is the user's to answer, in Clean tags.

    Returns {"order", "artist_title", "title_artist", "sure"}.
    """
    live = [r for r in recs if not r.protected]
    known = known_artists(live)
    tally = collections.Counter(
        v for v in (_vote(clean_stem(r.path), r.path, known) for r in live) if v)
    at, ta = tally[ARTIST_TITLE], tally[TITLE_ARTIST]
    return {"order": TITLE_ARTIST if ta > at else ARTIST_TITLE,
            "artist_title": at, "title_artist": ta, "sure": _settled(at, ta)}


def looks_swapped(recs):
    """Tracks whose artist tag is really the title, and vice versa.

    The evidence is the filename: its second half is an artist another file's
    tags name, its first half is nobody we know of, and this track's own tags
    follow that same wrong order. Everything found here is a suggestion for
    the user to confirm, never something Sortero acts on by itself.

    Only real tags count. A track whose names were read off the filename has
    nothing wrong written to it yet - that one is the name-order setting's job.
    """
    live = [r for r in recs if not r.protected]
    known = known_artists(live)
    out = []
    for r in live:
        if not (r.artist and r.title) or r.artist_from_name or r.title_from_name:
            continue
        stem = clean_stem(r.path)
        left, right = halves(stem)
        if not left or _vote(stem, r.path, known) != TITLE_ARTIST:
            continue
        if fold(r.artist) == fold(left) and fold(r.title) == fold(right):
            out.append(r)
    return out


def health(recs):
    """Summary stats the dashboard renders."""
    live = [r for r in recs if not r.protected]
    n = len(live) or 1
    spam_genre = [r for r in live if r.genre and is_spam(r.genre)]
    spam_comment = [r for r in live if r.comment and is_spam(r.comment)]
    h = {
        "total": len(recs),
        "active": len(live),
        "protected": len(recs) - len(live),
        "bytes": sum(r.size for r in recs),
        "analyzed": sum(1 for r in live if r.analyzed),
        "needs_analysis": [r for r in live if not r.analyzed],
        "no_genre": [r for r in live if not r.genre or is_spam(r.genre)],
        "no_artist": [r for r in live if not r.artist],
        "spam_genre": spam_genre,
        "spam_comment": spam_comment,
        "no_energy": [r for r in live if r.energy is None],
        "low_bitrate": [r for r in live if 0 < r.bitrate < 192000],
        "swapped": looks_swapped(live),
        "name_order": name_order_votes(live),
        "genres": collections.Counter(r.genre for r in live if r.genre and not is_spam(r.genre)),
        "keys": collections.Counter(r.camelot for r in live if r.camelot),
        "tops": collections.Counter(r.top for r in recs),
    }
    h["pct_analyzed"] = 100.0 * h["analyzed"] / n
    h["pct_genre"] = 100.0 * (len(live) - len(h["no_genre"])) / n
    h["pct_energy"] = 100.0 * (len(live) - len(h["no_energy"])) / n
    return h
