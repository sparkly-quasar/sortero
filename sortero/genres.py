"""Genre lookup and bulk assignment.

Sortero can only infer a genre from what the files already carry, and plenty of
them carry nothing - an analysis tool strips the tag, a Bandcamp rip never had
one. Two ways out: ask Discogs, whose *styles* are the subgenre granularity a DJ
wants ("Deep House", "Minimal Techno"), and failing that, set it by hand in
bulk.

Discogs' search works without an API token, but the free tier is rate limited,
so lookups are paced and cached on disk - re-running never re-asks.
"""
import json, os, re, time, urllib.parse, urllib.request

from . import net, paths
from .journal import Journal
from .organize import canon_genre
from .tagio import Track
from .version import __version__

UA = f"Sortero/{__version__} +https://github.com/sparkly-quasar/sortero"
SEARCH = "https://api.discogs.com/database/search"
MIN_INTERVAL = 2.5          # unauthenticated Discogs allows ~25 requests/minute
CACHE = "discogs-cache.json"

_last_call = [0.0]


class LookupError(Exception):
    pass


# ------------------------------------------------------------------- cache
def _cache_path():
    return os.path.join(paths.data_dir(), CACHE)


def load_cache():
    try:
        with open(_cache_path()) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(c):
    with open(_cache_path(), "w") as fh:
        json.dump(c, fh, indent=1)


# ------------------------------------------------------------- query shaping
VERSIONISH = re.compile(
    r"\s*[\(\[][^)\]]*\b(mix|remix|edit|version|extended|original|dub|radio|"
    r"instrumental|vip|rework|bootleg)\b[^)\]]*[\)\]]", re.I)


def clean_title(t):
    """Discogs matches the work, not the pressing - drop version descriptors."""
    t = re.sub(r"_PN\b", "", t or "")
    t = VERSIONISH.sub("", t)
    t = re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", t)
    t = re.sub(r"(?i)\s*(www\.\S+|\S+\.com)\s*", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -–")


def clean_artist(a):
    """First credited artist only; collaborations rarely match as written."""
    a = re.split(r"\s*(?:,|&|feat\.?|ft\.?|vs\.?|\bwith\b)\s*", a or "", maxsplit=1)[0]
    return re.sub(r"\s{2,}", " ", a).strip()


def key_for(rec):
    return f"{clean_artist(rec.artist)}|{clean_title(rec.title)}".lower()


# ------------------------------------------------------------------ lookup
def _throttle():
    wait = MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def _search(artist, title, timeout=25):
    q = urllib.parse.urlencode({"artist": artist, "track": title,
                                "type": "release", "per_page": "5"})
    req = urllib.request.Request(f"{SEARCH}?{q}", headers={"User-Agent": UA})
    for attempt in range(3):
        _throttle()
        try:
            with net.urlopen(req, timeout=timeout) as r:
                return (json.loads(r.read().decode()).get("results") or [])
        except urllib.error.HTTPError as e:
            if e.code == 429:                      # rate limited - back off
                time.sleep(5 * (attempt + 1))
                continue
            raise LookupError(f"Discogs returned {e.code}")
        except Exception as e:
            if attempt == 2:
                raise LookupError(str(e))
            time.sleep(2)
    raise LookupError("Discogs kept rate-limiting the request.")


def lookup(rec, cache=None):
    """Return (suggested_canonical_genre, raw_styles). Cached on disk."""
    cache = load_cache() if cache is None else cache
    k = key_for(rec)
    if not k.strip("|"):
        return None, []
    if k in cache:
        raw = cache[k]
    else:
        a, t = clean_artist(rec.artist), clean_title(rec.title)
        if not a or not t:
            return None, []
        res = _search(a, t)
        raw = []
        if res:
            raw = (res[0].get("style") or []) + (res[0].get("genre") or [])
        cache[k] = raw
    for value in raw:
        g = canon_genre(value, detail="fine") or canon_genre(value)
        if g:
            return g, raw
    return None, raw


def bulk_lookup(recs, detail="fine", progress=None, log=print):
    """Look up many tracks. Returns {path: (suggestion, raw_styles)}."""
    cache = load_cache()
    out = {}
    total = len(recs) or 1
    try:
        for i, r in enumerate(recs):
            if progress:
                progress(i, total)
            try:
                g, raw = lookup(r, cache)
            except LookupError as e:
                log(f"stopped: {e}")
                break
            if g or raw:
                out[r.path] = (g, raw)
    finally:
        save_cache(cache)
        if progress:
            progress(total, total)
    log(f"looked up {len(out)} of {total} tracks")
    return out


# -------------------------------------------------------------- assignment
def apply_genres(root, pairs, log=print, progress=None):
    """pairs: [(Rec, genre)] - writes the tag, journalled so it can be undone."""
    j = Journal("set-genre", root)
    total = len(pairs) or 1
    n = failed = 0
    for i, (rec, genre) in enumerate(pairs):
        if progress and i % 10 == 0:
            progress(i, total)
        t = Track(rec.path)
        if not t.ok:
            failed += 1
            continue
        old = t.get("genre")
        t.set("genre", genre)
        if t.save():
            j.tagged(rec.path, {"genre": {"old": old, "new": genre}})
            n += 1
        else:
            failed += 1
    if progress:
        progress(total, total)
    path = j.save()
    log(f"set genre on {n} tracks ({failed} failed) | journal: {path}")
    return path, n, failed
