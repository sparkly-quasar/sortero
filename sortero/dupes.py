"""Conservative duplicate detection.

Deliberately preserves distinct remixes: '(Raxon Remix)' and '(Truncate Remix)'
are different records and must never collapse into one group.
"""
import os, re, hashlib, collections, shutil
from .common import clean_stem
from .journal import Journal

NOISE = re.compile(
    r"(?i)\b(official\s*(music\s*)?video|official\s*audio|lyrics?\s*video|"
    r"hq|hd|free\s*download|out\s*now|full\s*version)\b")
LOSSLESS = {".flac", ".wav", ".aiff", ".aif"}


def norm_title(t):
    """Normalise a title while KEEPING the version/remix descriptor."""
    t = (t or "").lower()
    t = NOISE.sub(" ", t)
    t = t.replace("[", "(").replace("]", ")")
    t = re.sub(r"\(\s*(original|orig)\.?\s*(mix|version)?\s*\)", " ", t)
    t = re.sub(r"\bfeat\.?\b|\bft\.?\b", "feat", t)
    t = re.sub(r"[^a-z0-9()]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def norm_artist(a):
    a = re.sub(r"[^a-z0-9]+", " ", (a or "").lower())
    return " ".join(sorted(set(a.split())))


def ident(r):
    if r.title:
        return norm_artist(r.artist) + " :: " + norm_title(r.title)
    return "fn :: " + norm_title(clean_stem(r.path))


def _sig(path, size):
    """SHA1 of 512 KB from the middle of the file - a cheap content signature."""
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, size // 2 - 262144))
            return hashlib.sha1(fh.read(524288)).hexdigest()
    except OSError:
        return None


def set_name(r):
    """The playlist a copy's folder becomes when it loses that copy, or None.

    A folder somebody put together - a gig set, a vibe import - keeps its
    tracks as playlist entries. organize treats Sets/ as its own filing, since
    a reorganise can keep gig sets there as folders, but to the set itself a
    track going missing is exactly the loss this is here to prevent.
    """
    from .organize import folder_playlist_name, safe, SETS_DIR
    folder = os.path.dirname(r.rel)
    parts = folder.split(os.sep)
    if parts[0] == SETS_DIR and len(parts) > 1:
        return safe(folder.replace(os.sep, " - "), 100)
    return folder_playlist_name(r)


def _filed(r):
    """Is this copy in a genre folder, where a track lives?"""
    from .organize import is_filing_folder
    return not set_name(r) and is_filing_folder(os.path.dirname(r.rel))


def keeper(group):
    """Best copy: lossless first, then longer, then richer tags, then the one in
    a genre folder, then shorter path."""
    def score(r):
        return (r.ext in LOSSLESS,
                round(r.duration or 0),
                sum(1 for f in (r.artist, r.title, r.genre, r.key, r.bpm) if f),
                _filed(r),
                -len(r.rel))
    return max(group, key=score)


def find(recs, progress=None):
    """Return dict with 'exact' and 'likely' duplicate groups."""
    live = [r for r in recs if not r.protected]

    # --- exact: identical size AND identical content signature ---
    by_size = collections.defaultdict(list)
    for r in live:
        if r.size:
            by_size[r.size].append(r)
    candidates = [g for g in by_size.values() if len(g) > 1]
    exact = []
    total = sum(len(g) for g in candidates) or 1
    done = 0
    for g in candidates:
        byhash = collections.defaultdict(list)
        for r in g:
            byhash[_sig(r.path, r.size)].append(r)
            done += 1
            if progress and done % 20 == 0:
                progress(done, total)
        for h, sub in byhash.items():
            if h and len(sub) > 1:
                exact.append(sub)
    if progress:
        progress(total, total)

    in_exact = {r.path for g in exact for r in g}

    # --- likely: same artist/title/version and near-identical duration ---
    by_ident = collections.defaultdict(list)
    for r in live:
        k = ident(r)
        if len(k.replace("fn ::", "").strip()) > 5:
            by_ident[k].append(r)

    likely = []
    for k, g in by_ident.items():
        if len(g) < 2:
            continue
        g = sorted(g, key=lambda r: r.duration or 0)
        bucket, cur = [], [g[0]]
        for r in g[1:]:
            if abs((r.duration or 0) - (cur[-1].duration or 0)) <= 3:
                cur.append(r)
            else:
                bucket.append(cur); cur = [r]
        bucket.append(cur)
        for b in bucket:
            if len(b) > 1 and not all(x.path in in_exact for x in b):
                likely.append(b)

    return {"exact": exact, "likely": likely}


def canonical_map(found, include_likely=True):
    """{duplicate_path: keeper_path} for every extra copy.

    Feed this to organize.plan so playlists repoint at the surviving file
    instead of at a copy that is about to move into _Quarantine.
    """
    m = {}
    for g in found["exact"] + (found["likely"] if include_likely else []):
        k = keeper(g)
        for r in g:
            if r is not k:
                m[r.path] = k.path
    return m


def reclaimable(groups):
    return sum(sum(r.size for r in g if r is not keeper(g)) for g in groups)


def _unique(path):
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(path):
        n += 1
        path = f"{stem} ({n}){ext}"
    return path


def plan(groups, one_folder=True):
    """What happens to every copy. Returns [(group, keeper, home folder)].

    The keeper normally stays put. With one_folder, a keeper sitting in a set or
    other curated folder moves into the genre folder another copy is in, so the
    track lives where it is filed and the set keeps it as a playlist entry.
    """
    out = []
    for g in groups:
        k = keeper(g)
        home = os.path.dirname(k.path)
        if one_folder and set_name(k):
            filed = [r for r in g if r is not k and _filed(r)]
            if filed:
                home = os.path.dirname(filed[0].path)
        out.append((g, k, home))
    return out


def describe(r, k, home, one_folder=True):
    """One short phrase for the table: what this copy is about to go through."""
    if r is k:
        if home != os.path.dirname(k.path):
            return "keep, moves to " + os.path.basename(home)
        return "keep"
    name = set_name(r) if one_folder else None
    return f"set aside; playlist '{name}'" if name else "set aside"


def quarantine(root, groups, log=print, recs=None, one_folder=False, progress=None):
    """Move non-keepers into _Quarantine (reversible). Never deletes.

    Playlists always follow the copy that stays. With one_folder, every set or
    other curated folder that loses a copy is saved as a playlist with the kept
    copy in that copy's place, and a keeper found only in a curated folder moves
    into the genre folder another copy was filed in. `recs` (the whole scan)
    is what those playlists are built from. Returns (journal path, files set aside).
    """
    from .organize import QUARANTINE
    from .journal import prune_empty
    from .library import PROTECTED
    from . import playlists

    j = Journal("dedupe", root)
    qdir = os.path.join(root, QUARANTINE, "duplicates")
    remap, lost_from = {}, collections.defaultdict(set)   # playlist name -> paths gone
    n = 0
    steps = plan(groups, one_folder)
    total = len(steps) or 1
    for i, (g, k, home) in enumerate(steps):
        if progress and i % 10 == 0:
            progress(i, total)
        gone = []
        for r in g:
            if r is k:
                continue
            try:
                rel = os.path.relpath(r.path, root).replace(os.sep, "__")
                dest = _unique(os.path.join(qdir, rel))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(r.path, dest)
                j.moved(r.path, dest)
                gone.append(r)
                n += 1
            except Exception as e:
                log(f"  ! {os.path.basename(r.path)}: {e}")
        final = k.path
        if home != os.path.dirname(k.path):
            try:
                final = _unique(os.path.join(home, os.path.basename(k.path)))
                shutil.move(k.path, final)
                j.moved(k.path, final)
                gone.append(k)
            except Exception as e:
                final = k.path
                log(f"  ! {os.path.basename(k.path)}: {e}")
        for r in gone:
            if r is not k or final != k.path:
                remap[r.path] = final
            name = set_name(r) if one_folder else None
            if name:
                lost_from[name].add(r.path)

    # Playlists that pointed at a copy now point at the one that stayed.
    if remap:
        playlists.remap(root, remap, journal=j)

    # Each curated folder that lost a copy keeps the track as a playlist entry,
    # in the place the file had in that folder.
    if lost_from:
        members = collections.defaultdict(list)
        for r in recs or [r for g in groups for r in g]:
            name = set_name(r)
            if name in lost_from and not r.protected:
                members[name].append(r.path)
        d = playlists.playlist_dir(root)
        for name in sorted(lost_from):
            wanted = []
            for p in sorted(members[name], key=lambda p: os.path.basename(p).lower()):
                p = remap.get(p, p)
                if p not in wanted:
                    wanted.append(p)
            fp = os.path.join(d, f"{name}.m3u8")
            have, old = [], None
            if os.path.exists(fp):
                with open(fp, encoding="utf-8") as fh:
                    old = fh.read()
                have = [l.strip() for l in old.splitlines()
                        if l.strip() and not l.startswith("#")]
            rels = have + [x for x in (os.path.relpath(p, d) for p in wanted)
                           if x not in have]
            os.makedirs(d, exist_ok=True)
            if old is None:
                j.created(fp)
            else:
                j.wrote(fp, old)
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write("#EXTM3U\n" + "".join(x + "\n" for x in rels))

    prune_empty(root, keep=PROTECTED)
    if progress:
        progress(total, total)
    path = j.save()
    log(f"quarantined {n} duplicate files, {len(lost_from)} playlists kept | journal: {path}")
    return path, n
