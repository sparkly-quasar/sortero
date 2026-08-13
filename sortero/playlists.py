"""Build .m3u8 playlists from streaming-service tracklists.

Sources, in order of convenience:
  * a Spotify playlist/album URL - read from the public embed payload
  * a CSV export (Exportify, TuneMyMusic and friends)
  * pasted 'Artist - Title' lines - always works, including for Tidal

Tidal renders its playlists client-side and its API needs authentication, so a
bare Tidal URL cannot be resolved; the UI says so and points at the fallbacks.
"""
import os, re, csv, json, difflib, urllib.request, urllib.error, collections

from . import net
from .dupes import norm_artist, norm_title
from .organize import PLAYLIST_DIR, safe

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
SPOTIFY_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?(playlist|album)/([A-Za-z0-9]+)")
TIDAL_RE = re.compile(r"tidal\.com/(?:browse/)?(playlist|album)/([0-9a-f-]{16,})")
# The embed payload is capped; hitting exactly this many means there may be more.
EMBED_CAP = 50


class SourceError(Exception):
    pass


# ---------------------------------------------------------------- fetching
def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with net.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _find_tracklist(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("trackList"), list):
            return obj["trackList"]
        for v in obj.values():
            r = _find_tracklist(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_tracklist(v)
            if r:
                return r
    return None


def from_spotify(kind, sid):
    html = _get(f"https://open.spotify.com/embed/{kind}/{sid}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise SourceError("Spotify returned a page with no track data. "
                          "Make sure the playlist is public, or paste the tracklist instead.")
    tracks = _find_tracklist(json.loads(m.group(1))) or []
    if not tracks:
        raise SourceError("No tracks found. Is the playlist public?")
    name = None
    try:
        d = json.loads(m.group(1))
        name = (d.get("props", {}).get("pageProps", {})
                 .get("state", {}).get("data", {}).get("entity", {}).get("name"))
    except Exception:
        pass
    out = [(t.get("subtitle") or "", t.get("title") or "") for t in tracks]
    return name, [x for x in out if x[1]]


# ------------------------------------------------- authenticated (full lists)
def _artists_from_included(included):
    """id -> artist name, from a JSON:API 'included' block."""
    out = {}
    for inc in included or []:
        if inc.get("type") == "artists":
            out[inc.get("id")] = (inc.get("attributes") or {}).get("name")
    return out


def from_tidal_api(pid, log=print):
    """Read a full TIDAL playlist via the official API. Requires connecting first."""
    from . import auth
    name = None
    try:
        meta = auth.api_get("tidal", f"/playlists/{pid}", {"countryCode": "US"}, log=log)
        name = ((meta.get("data") or {}).get("attributes") or {}).get("title")
    except auth.AuthError:
        pass

    entries, url = [], f"/playlists/{pid}/relationships/items"
    params = {"countryCode": "US", "include": "items,items.artists", "page[limit]": "100"}
    seen = 0
    while url and seen < 50:                      # hard stop on runaway pagination
        doc = auth.api_get("tidal", url, params, log=log)
        artists = _artists_from_included(doc.get("included"))
        for inc in doc.get("included") or []:
            if inc.get("type") != "tracks":
                continue
            a = inc.get("attributes") or {}
            names = [artists.get(r.get("id")) for r in
                     ((inc.get("relationships") or {}).get("artists") or {}).get("data", [])]
            names = [n for n in names if n]
            if a.get("title"):
                entries.append((", ".join(names), a["title"]))
        nxt = (doc.get("links") or {}).get("next")
        url, params, seen = (nxt, None, seen + 1) if nxt else (None, None, seen)

    if not entries:
        raise SourceError(
            "TIDAL returned no tracks for that playlist. It may be private to "
            "another account, or the link may be an album rather than a playlist.\n\n"
            "You can always paste the tracklist instead.")
    return name, entries


def from_spotify_api(kind, sid, log=print):
    """Read a full Spotify playlist via the API - no 50-track embed limit."""
    from . import auth
    name, entries = None, []
    if kind == "playlist":
        try:
            meta = auth.api_get("spotify", f"/playlists/{sid}", {"fields": "name"}, log=log)
            name = meta.get("name")
        except auth.AuthError:
            pass
        url = f"/playlists/{sid}/tracks"
        params = {"limit": "100", "fields":
                  "next,items(track(name,artists(name)))"}
    else:
        url = f"/albums/{sid}/tracks"
        params = {"limit": "50"}
    pages = 0
    while url and pages < 50:
        doc = auth.api_get("spotify", url, params, log=log)
        for it in doc.get("items") or []:
            t = it.get("track") if "track" in it else it
            if not t or not t.get("name"):
                continue
            entries.append((", ".join(a.get("name", "") for a in t.get("artists") or []),
                            t["name"]))
        url, params, pages = doc.get("next"), None, pages + 1
    if not entries:
        raise SourceError("Spotify returned no tracks for that link.")
    return name, entries


def from_url(url, log=print):
    """Return (suggested_name, [(artist, title), ...]).

    Uses the official API when that service is connected (full playlist, any
    length); otherwise falls back to the public embed, which Spotify caps.
    """
    from . import auth
    url = url.strip()

    m = SPOTIFY_RE.search(url)
    if m:
        if auth.is_connected("spotify"):
            try:
                return from_spotify_api(m.group(1), m.group(2), log=log)
            except auth.AuthError as e:
                log(f"Spotify API failed ({e}); falling back to the public embed.")
        return from_spotify(m.group(1), m.group(2))

    m = TIDAL_RE.search(url)
    if m:
        if not auth.is_connected("tidal"):
            raise SourceError(
                "TIDAL needs to be connected before it can read a playlist link.\n\n"
                "Click 'Connect TIDAL…' below (one-time setup: create a free app at "
                "developer.tidal.com and paste its Client ID).\n\n"
                "Or paste the tracklist as 'Artist - Title' lines instead.")
        if m.group(1) != "playlist":
            raise SourceError("That's a TIDAL album link; paste a playlist link.")
        return from_tidal_api(m.group(2), log=log)

    raise SourceError("Unrecognised link. Paste a Spotify or TIDAL playlist URL, "
                      "or paste the tracklist as 'Artist - Title' lines.")


# ---------------------------------------------------------------- parsing
def from_csv(path):
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SourceError("That CSV has no rows.")
    cols = {k.lower().strip(): k for k in rows[0].keys() if k}

    def pick(*cands):
        for c in cands:
            for low, orig in cols.items():
                if c in low:
                    return orig
        return None

    tcol = pick("track name", "title", "track", "song", "name")
    acol = pick("artist name", "artist", "performer")
    if not tcol:
        raise SourceError(f"Couldn't find a title column in: {', '.join(cols.values())}")
    out = []
    for r in rows:
        t = (r.get(tcol) or "").strip()
        a = (r.get(acol) or "").strip() if acol else ""
        if t:
            out.append((a, t))
    return os.path.splitext(os.path.basename(path))[0], out


LINE_SPLIT = re.compile(r"\s+[-–—]\s+|\s+[-–—]|\t")


def from_text(text):
    """Parse pasted lines. Handles 'Artist - Title', 'Artist\\tTitle', numbered lists."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^\s*\d{1,3}[.)]\s+", "", line)      # 1. / 12)
        line = re.sub(r"\s+\d+:\d{2}\s*$", "", line)        # trailing duration
        parts = LINE_SPLIT.split(line, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            out.append((parts[0].strip(), parts[1].strip()))
        else:
            out.append(("", line))
    if not out:
        raise SourceError("Nothing to read - paste one track per line.")
    return None, out


# ---------------------------------------------------------------- matching
def _title_forms(title, artist):
    """Normalised titles to index/query under.

    Tags are inconsistent about whether the artist is repeated in the title
    ('Josh Butler - Keep It Hot' as the *title*, with artist 'Josh Butler').
    Indexing both forms means either spelling finds the track.
    """
    from .organize import _strip_artist_prefix
    forms = {norm_title(title or "")}
    stripped = _strip_artist_prefix(title or "", artist or "")
    forms.add(norm_title(stripped))
    return {f for f in forms if f}


def _index(recs):
    exact, by_title = {}, collections.defaultdict(list)
    for r in recs:
        if r.protected:
            continue
        na = norm_artist(r.artist or "")
        for t in _title_forms(r.title, r.artist):
            exact.setdefault(na + "|" + t, r)
            if r not in by_title[t]:
                by_title[t].append(r)
    return exact, by_title


def _strip_version(t):
    return re.sub(r"\s*\([^)]*\)\s*$", "", t).strip() or t


def match(entries, recs, cutoff=0.86):
    """Match (artist, title) pairs to library records.

    Returns [{'artist','title','rec','how'}]; rec is None when unmatched.
    """
    exact, by_title = _index(recs)
    titles = list(by_title.keys())
    results = []
    for artist, title in entries:
        na = norm_artist(artist)
        forms = sorted(_title_forms(title, artist), key=len)
        nt = forms[-1] if forms else ""
        rec, how = None, "missing"

        # try every spelling of the title before giving up
        for cand in forms:
            r = exact.get(na + "|" + cand)
            if r:
                rec, how, nt = r, "exact", cand
                break
        if rec is None:
            for cand in forms:
                if cand in by_title:
                    nt = cand
                    break

        if rec is None and nt:
            r = exact.get(na + "|" + nt)
            if r:
                rec, how = r, "exact"
            elif nt in by_title:
                cands = by_title[nt]
                if na:
                    at = set(na.split())
                    scored = [(len(at & set(norm_artist(c.artist or "").split())), c)
                              for c in cands]
                    scored.sort(key=lambda x: -x[0])
                    if scored[0][0] > 0:
                        rec, how = scored[0][1], "title+artist"
                    else:
                        rec, how = cands[0], "title only"
                else:
                    rec, how = cands[0], "title only"

        # remix/version wording differs -> fuzzy on the bare title
        if rec is None and nt:
            base = norm_title(_strip_version(title))
            close = difflib.get_close_matches(base or nt, titles, n=3, cutoff=cutoff)
            for c in close:
                cands = by_title[c]
                if na:
                    at = set(na.split())
                    for cand in cands:
                        if at & set(norm_artist(cand.artist or "").split()):
                            rec, how = cand, "fuzzy"
                            break
                if rec:
                    break
            if rec is None and close and not na:
                rec, how = by_title[close[0]][0], "fuzzy"

        results.append({"artist": artist, "title": title, "rec": rec, "how": how})
    return results


# ---------------------------------------------------------------- writing
def playlist_dir(root):
    return os.path.join(root, PLAYLIST_DIR)


def existing(root):
    d = playlist_dir(root)
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".m3u8"))


def write(root, name, paths, journal=None):
    d = playlist_dir(root)
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, safe(name, 100) + ".m3u8")
    with open(fp, "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n")
        for p in paths:
            fh.write(os.path.relpath(p, d) + "\n")
    if journal:
        journal.created(fp)
    return fp


def repair(root, recs, dry=True, log=print):
    """Re-link playlist entries whose file has moved or been re-encoded.

    A track that goes away for analysis comes back renamed, in a new format, at
    a new path - every playlist that referenced it is left pointing at nothing.
    Rather than guess from paths, this matches the old filename's artist/title
    against the library as it stands now, which survives both the rename and the
    format change.

    Returns (fixed, unresolved, per_playlist) without writing when dry=True.
    """
    from .common import clean_stem, split_artist_title
    from .library import Rec

    d = playlist_dir(root)
    if not os.path.isdir(d):
        return 0, 0, {}

    exact, by_title = _index(recs)
    fixed = unresolved = 0
    per = {}

    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".m3u8"):
            continue
        fp = os.path.join(d, fn)
        with open(fp, encoding="utf-8") as fh:
            lines = [l.rstrip("\n") for l in fh]

        out, changed, gone = [], 0, 0
        for line in lines:
            s = line.strip()
            if not s or s.startswith("#"):
                out.append(line)
                continue
            target = os.path.normpath(os.path.join(d, s))
            if os.path.exists(target):
                out.append(line)
                continue

            stem = clean_stem(target)
            a, t = split_artist_title(stem)
            probe = Rec(path=target, rel=os.path.basename(target), artist=a, title=t)
            hit = match([(a or "", t or stem)], recs)[0]
            if hit["rec"]:
                out.append(os.path.relpath(hit["rec"].path, d))
                changed += 1
            else:
                out.append(line)          # leave it; better than dropping it
                gone += 1

        fixed += changed
        unresolved += gone
        if changed or gone:
            per[fn] = (changed, gone)
        if changed and not dry:
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(out) + "\n")

    log(f"{'would re-link' if dry else 're-linked'} {fixed} entries; "
        f"{unresolved} still unmatched")
    return fixed, unresolved, per


def append(root, name, paths):
    """Add paths to a playlist, creating it if needed and skipping repeats."""
    d = playlist_dir(root)
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, safe(name, 100) + ".m3u8")
    have = []
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as fh:
            have = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    rels = [os.path.relpath(p, d) for p in paths]
    added = [r for r in rels if r not in have]
    with open(fp, "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n")
        for r in have + added:
            fh.write(r + "\n")
    return fp, len(added)
