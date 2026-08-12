"""Scan a DJ collection into track records and compute library health."""
import os, re, collections
from dataclasses import dataclass, field
from .common import AUDIO_EXTS, is_spam, clean_stem, split_artist_title, to_camelot
from .tagio import Track

# Folders Sortero never touches. The user's Platinum Notes / Mixed In Key staging
# areas, plus Sortero's own quarantine.
PROTECTED = {"To Be Processed", "Processed", "_Quarantine", "_Playlists"}

# Folders that hold full releases / recordings rather than individual DJ tracks.
NON_TRACK_HINTS = re.compile(r"(?i)^(recorded mixes|renaissance|compilations)")


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
    album: str = None
    duration: float = 0.0
    protected: bool = False

    @property
    def top(self):
        return self.rel.split(os.sep)[0] if os.sep in self.rel else "(root)"

    @property
    def camelot(self):
        return to_camelot(self.key) or (self.key if _is_camelot(self.key) else None)

    @property
    def energy(self):
        for src in (self.grouping, self.comment):
            if src:
                m = re.search(r"(?i)energy\s*(\d+)", src)
                if m:
                    return int(m.group(1))
        return None

    @property
    def analyzed(self):
        return bool(self.key and self.bpm)

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


def scan(root, progress=None):
    """Walk root and read tags. Returns list[Rec]."""
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
            r.album = _clean(t.get("album"))
            r.duration = t.length or 0.0
        if not r.artist or not r.title:
            a, ti = split_artist_title(clean_stem(p))
            r.artist = r.artist or a
            r.title = r.title or ti
        recs.append(r)
    if progress:
        progress(total, total)
    return recs


def _clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


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
        "low_bitrate": [],
        "genres": collections.Counter(r.genre for r in live if r.genre and not is_spam(r.genre)),
        "keys": collections.Counter(r.camelot for r in live if r.camelot),
        "tops": collections.Counter(r.top for r in recs),
    }
    h["pct_analyzed"] = 100.0 * h["analyzed"] / n
    h["pct_genre"] = 100.0 * (len(live) - len(h["no_genre"])) / n
    h["pct_energy"] = 100.0 * (len(live) - len(h["no_energy"])) / n
    return h
