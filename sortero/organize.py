"""Plan and apply the library reorganization.

Design notes
------------
The layout follows the consensus of working-DJ practice: keep the *filesystem*
shallow and predictable, and let key/BPM/energy live in tags where the DJ
software can sort on them. Existing hand-curated folders (gig sets, albums) are
preserved as M3U8 playlists first, so no curation is lost when files move to
their canonical home.

    <root>/
      Tracks/<Genre>/Artist - Title.ext     canonical home for every DJ track
      Sets/<Set Name>/                      preserved gig folders (optional)
      Albums/<Album>/                       full releases, left intact
      Mixes/                                long-form recordings
      _Playlists/*.m3u8                     every old folder, as a playlist
      To Be Processed/  Processed/          never touched
      _Quarantine/                          duplicates and rejects
"""
import os, re, shutil, collections, unicodedata
from .common import is_spam, clean_stem
from .library import PROTECTED, Rec
from .journal import Journal, prune_empty

TRACKS_DIR = "Tracks"
SETS_DIR = "Sets"
ALBUMS_DIR = "Albums"
MIXES_DIR = "Mixes"
PLAYLIST_DIR = "_Playlists"
QUARANTINE = "_Quarantine"
UNSORTED = "Unsorted"

MIX_MIN_SECONDS = 20 * 60          # longer than this and it is a mix, not a track

# Canonical genre vocabulary, ordered: first match wins.
GENRE_RULES = [
    (r"melodic (house|techno)", "Melodic House & Techno"),
    (r"hypnotic|peak.?time|driving|hard techno", "Techno"),
    (r"\btechno\b", "Techno"),
    (r"minimal.*deep tech|deep tech|\bminimal\b", "Minimal & Deep Tech"),
    (r"tech.?house", "Tech House"),
    (r"afro.?house", "Afro House"),
    (r"organic house|downtempo|lounge|ambient|drone", "Downtempo & Organic"),
    (r"progressive|\bprog\b", "Progressive House"),
    (r"deep house", "Deep House"),
    (r"nu.?disco|indie dance|\bdisco\b|funky house", "Nu Disco & Indie Dance"),
    (r"\bhouse\b", "House"),
    (r"idm|intelligent|electronica|experimental", "Electronica & IDM"),
    (r"breaks|breakbeat|\bbass\b|garage|dubstep", "Breaks & Bass"),
    (r"hip.?hop|\brap\b|trap", "Hip-Hop"),
    (r"chill|lo.?fi|jazz|soul|funk", "Chill & Funk"),
    (r"\bpop\b|dance|electro", "Dance & Pop"),
]

# Folder names that name a genre even though no GENRE_RULES pattern catches them.
FOLDER_HINTS = [
    (r"(?i)smooth vibes", "Chill & Funk"),
    (r"(?i)lez dance", "Dance & Pop"),
    (r"(?i)sherwood bangers", "Tech House"),
]

ILLEGAL = re.compile(r'[/:\x00-\x1f]')


def canon_genre(value, strict=False):
    """Map a genre string onto the canonical vocabulary.

    strict=True is used for *folder names*, where an unrecognised value is just
    a folder name ("Rew Import", "Compilations") and must never become a genre.
    """
    if not value or is_spam(value):
        return None
    low = value.lower()
    for pat, canon in GENRE_RULES:
        if re.search(pat, low):
            return canon
    if strict:
        return None
    v = value.strip()
    return v if len(v) <= 40 else None


def genre_from_folders(rel):
    """Infer a genre from the folders a track currently lives in.

    Walks deepest-first: 'Compilations/Beatport Best New House' should resolve
    from the specific chart name, not the generic parent.
    """
    for part in reversed(rel.split(os.sep)[:-1]):
        for pat, canon in FOLDER_HINTS:
            if re.search(pat, part):
                return canon
        c = canon_genre(part, strict=True)
        if c:
            return c
    return None


def resolve_genre(r):
    """Tag genre wins; otherwise infer from the folder it currently sits in."""
    return canon_genre(r.genre) or genre_from_folders(r.rel) or UNSORTED


def safe(name, maxlen=120):
    name = ILLEGAL.sub("-", str(name or "").strip())
    name = re.sub(r"\s{2,}", " ", name).strip(" .")
    return (name[:maxlen].strip() or "Untitled")


def _strip_artist_prefix(title, artist):
    """Drop a leading 'Artist - ' from a title so filenames don't say it twice.

    Plenty of tags carry 'Rodrigo Gallardo - El Abuelo' as the *title* while the
    artist field also says 'Rodrigo Gallardo'.
    """
    if not title or not artist:
        return title

    def norm(s):
        s = unicodedata.normalize("NFKD", s.lower())
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]+", "", s)

    a = norm(artist)
    if not a:
        return title
    for sep in (" - ", " – ", " — "):
        head, found, tail = title.partition(sep)
        if found and norm(head) == a and tail.strip():
            return tail.strip()
    return title


def target_filename(r):
    artist = r.artist or "Unknown Artist"
    title = _strip_artist_prefix(r.title or clean_stem(r.path), artist)
    return f"{safe(artist, 60)} - {safe(title, 90)}{r.ext}"


def classify(r, keep_sets=True, route_unanalyzed=False):
    """Return (category, subfolder) for a record."""
    top = r.top
    if r.protected:
        return ("skip", None)
    if r.duration and r.duration >= MIX_MIN_SECONDS:
        return ("mix", None)
    # Tracks with no key/BPM belong in the user's own PN/MIK staging area.
    if route_unanalyzed and not r.analyzed:
        return ("unanalyzed", None)
    if keep_sets and top == SETS_DIR:
        parts = r.rel.split(os.sep)
        return ("set", parts[1] if len(parts) > 2 else "Misc")
    if re.match(r"(?i)^(recorded mixes)$", top):
        return ("mix", None)
    if re.match(r"(?i)^(renaissance|compilations)", top) and r.album:
        return ("album", safe(r.album, 80))
    return ("track", resolve_genre(r))


# --------------------------------------------------------------------------
def plan(root, recs, keep_sets=True, min_genre=8, route_unanalyzed=False,
         canonical=None):
    """Build the list of moves. Returns (moves, playlists, stats).

    moves: list of (rec, dest_abs)
    playlists: {playlist_name: [dest_abs, ...]}  from the CURRENT folder layout

    `canonical` maps duplicate path -> the path of the copy being kept. Extra
    copies are routed to _Quarantine and every playlist that referenced one is
    repointed at the surviving file, so a track curated into five vibe folders
    ends up as one file on disk referenced by five playlists.
    """
    canonical = canonical or {}

    # First pass: classify and count genres so tiny genres can be folded away.
    prelim = {}
    gcount = collections.Counter()
    for r in recs:
        if canonical.get(r.path, r.path) != r.path:
            prelim[r.path] = ("duplicate", None)
            continue
        cat, sub = classify(r, keep_sets, route_unanalyzed)
        prelim[r.path] = (cat, sub)
        if cat == "track":
            gcount[sub] += 1

    small = {g for g, c in gcount.items() if c < min_genre and g != UNSORTED}

    moves, used = [], {}
    for r in recs:
        cat, sub = prelim[r.path]
        if cat == "skip":
            continue
        if cat == "duplicate":
            rel = os.path.relpath(r.path, root).replace(os.sep, "__")
            dest = os.path.join(root, QUARANTINE, "duplicates", rel)
            moves.append((r, dest))
            continue
        if cat == "track":
            if sub in small:
                sub = UNSORTED
            dest_dir = os.path.join(root, TRACKS_DIR, safe(sub, 60))
            fname = target_filename(r)
        elif cat == "set":
            dest_dir = os.path.join(root, SETS_DIR, safe(sub, 60))
            fname = target_filename(r)
        elif cat == "album":
            dest_dir = os.path.join(root, ALBUMS_DIR, safe(sub, 80))
            fname = os.path.basename(r.path)
        elif cat == "unanalyzed":
            dest_dir = os.path.join(root, "To Be Processed")
            fname = target_filename(r)
        else:  # mix
            dest_dir = os.path.join(root, MIXES_DIR)
            fname = os.path.basename(r.path)

        dest = os.path.join(dest_dir, fname)
        # de-collide within the plan
        n = 1
        stem, ext = os.path.splitext(dest)
        while dest.lower() in used and used[dest.lower()] != r.path:
            n += 1
            dest = f"{stem} ({n}){ext}"
        used[dest.lower()] = r.path
        if os.path.normpath(dest) != os.path.normpath(r.path):
            moves.append((r, dest))

    # Preserve every existing folder as a playlist of its post-move locations.
    # A duplicate resolves to wherever its canonical copy landed.
    final = {r.path: d for r, d in moves}

    def resolve(path):
        target = canonical.get(path, path)
        return final.get(target, target)

    playlists = collections.defaultdict(list)
    for r in recs:
        if r.protected:
            continue
        folder = os.path.dirname(r.rel)
        if not folder:
            continue
        name = safe(folder.replace(os.sep, " - "), 100)
        dest = resolve(r.path)
        if dest not in playlists[name]:      # same track curated twice in a folder
            playlists[name].append(dest)

    stats = {
        "moves": len(moves),
        "genres": collections.Counter(
            os.path.basename(os.path.dirname(d)) for r, d in moves
            if os.path.join(root, TRACKS_DIR) in d),
        "playlists": len(playlists),
        "skipped_protected": sum(1 for r in recs if r.protected),
        "deduped": sum(1 for r in recs if prelim.get(r.path, (None,))[0] == "duplicate"),
    }
    return moves, dict(playlists), stats


def playlists_from_current(root, recs):
    """Playlists mirroring where files actually are right now.

    Unlike plan(), this points at real current paths, so it is safe to call
    whether or not the library has been reorganised.
    """
    pls = collections.defaultdict(list)
    for r in recs:
        if r.protected:
            continue
        folder = os.path.dirname(r.rel)
        if not folder:
            continue
        name = safe(folder.replace(os.sep, " - "), 100)
        if r.path not in pls[name]:
            pls[name].append(r.path)
    return dict(pls)


def write_playlists(root, playlists, journal=None, dry=False, min_tracks=2):
    out = os.path.join(root, PLAYLIST_DIR)
    written = []
    for name, paths in sorted(playlists.items()):
        if len(paths) < min_tracks:
            continue
        fp = os.path.join(out, f"{name}.m3u8")
        written.append(fp)
        if dry:
            continue
        os.makedirs(out, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n")
            for p in paths:
                fh.write(os.path.relpath(p, out) + "\n")
        if journal:
            journal.created(fp)
    return written


def folder_playlist_name(rec):
    """The playlist name a track's current folder maps to, or None at the root."""
    folder = os.path.dirname(rec.rel)
    if not folder:
        return None
    return safe(folder.replace(os.sep, " - "), 100)


def stage_for_analysis(root, recs, all_recs=None, log=print, progress=None):
    """Move chosen tracks into 'To Be Processed' for key/BPM analysis.

    Their current folder membership is written to playlists first and recorded
    as pending, so filing them back out of 'Processed' restores them to the
    sets and vibe playlists they were curated into.
    """
    from . import membership

    j = Journal("stage-for-analysis", root)
    dest_dir = os.path.join(root, "To Be Processed")

    # Capture curation before anything moves.
    staged_paths = {r.path for r in recs}
    owed = [(r, [n]) for r in recs if (n := folder_playlist_name(r))]
    if owed:
        pool = all_recs if all_recs is not None else recs
        by_name = collections.defaultdict(list)
        for r in pool:
            if r.protected or r.path in staged_paths:
                continue
            n = folder_playlist_name(r)
            if n:
                by_name[n].append(r.path)
        affected = {n for _, names in owed for n in names}
        # min_tracks=1: keep even a nearly-empty playlist alive so the staged
        # track has something to rejoin.
        write_playlists(root, {n: by_name.get(n, []) for n in affected},
                        journal=j, min_tracks=1)
        membership.remember(owed)
        log(f"recorded playlist membership for {len(owed)} tracks "
            f"across {len(affected)} playlists")

    total = len(recs) or 1
    n = 0
    for i, r in enumerate(recs):
        if progress and i % 10 == 0:
            progress(i, total)
        try:
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, target_filename(r))
            stem, ext = os.path.splitext(dest)
            c = 1
            while os.path.exists(dest):
                c += 1
                dest = f"{stem} ({c}){ext}"
            shutil.move(r.path, dest)
            j.moved(r.path, dest)
            n += 1
        except Exception as e:
            log(f"  ! {os.path.basename(r.path)}: {e}")
    prune_empty(root, keep=PROTECTED)
    if progress:
        progress(total, total)
    path = j.save()
    log(f"staged {n} tracks in 'To Be Processed' | journal: {path}")
    return path, n


def apply(root, moves, playlists, log=print, progress=None):
    """Execute the plan. Returns the saved journal path."""
    j = Journal("organize", root)
    total = len(moves)
    remap = {}                      # planned dest -> path actually written
    for i, (r, dest) in enumerate(moves):
        if progress and i % 20 == 0:
            progress(i, total)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            final = dest
            n = 1
            stem, ext = os.path.splitext(dest)
            while os.path.exists(final):
                n += 1
                final = f"{stem} ({n}){ext}"
            shutil.move(r.path, final)
            j.moved(r.path, final)
            if final != dest:
                remap[dest] = final
        except Exception as e:
            log(f"  ! {os.path.basename(r.path)}: {e}")
    # playlists reference post-move paths; honour any collision renames
    if remap:
        playlists = {name: [remap.get(p, p) for p in paths]
                     for name, paths in playlists.items()}
    write_playlists(root, playlists, journal=j)
    prune_empty(root, keep=PROTECTED)
    if progress:
        progress(total, total)
    path = j.save()
    log(f"moved {len(j.entries)} items | journal: {path}")
    return path
