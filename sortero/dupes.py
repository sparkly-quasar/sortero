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


def keeper(group):
    """Best copy: lossless first, then longer, then richer tags, then shorter path."""
    def score(r):
        return (r.ext in LOSSLESS,
                round(r.duration or 0),
                sum(1 for f in (r.artist, r.title, r.genre, r.key, r.bpm) if f),
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


def quarantine(root, groups, log=print):
    """Move non-keepers into _Quarantine (reversible). Never deletes."""
    from .organize import QUARANTINE
    j = Journal("dedupe", root)
    qdir = os.path.join(root, QUARANTINE, "duplicates")
    n = 0
    for g in groups:
        k = keeper(g)
        for r in g:
            if r is k:
                continue
            try:
                rel = os.path.relpath(r.path, root).replace(os.sep, "__")
                dest = os.path.join(qdir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                stem, ext = os.path.splitext(dest)
                i = 1
                while os.path.exists(dest):
                    i += 1
                    dest = f"{stem} ({i}){ext}"
                shutil.move(r.path, dest)
                j.moved(r.path, dest)
                n += 1
            except Exception as e:
                log(f"  ! {os.path.basename(r.path)}: {e}")
    path = j.save()
    log(f"quarantined {n} duplicate files | journal: {path}")
    return path, n
