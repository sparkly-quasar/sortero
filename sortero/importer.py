"""Intake for new music.

Rules, in order:
  1. Already in the library (same content signature) -> flagged as duplicate.
  2. No key/BPM               -> 'To Be Processed'  (your Platinum Notes / MIK stage).
  3. Otherwise               -> Tracks/<Genre>/Artist - Title.ext
Spam tags are stripped on the way in so junk never enters the library.
"""
import os, shutil, collections
from .common import AUDIO_EXTS, is_spam
from .library import scan, Rec, is_protected
from .organize import resolve_genre, target_filename, safe, TRACKS_DIR, MIX_MIN_SECONDS, MIXES_DIR
from .dupes import _sig, ident
from .journal import Journal
from .tagio import Track
from . import membership
from . import playlists as pl

TO_PROCESS = "To Be Processed"
PROCESSED = "Processed"


def library_excluding(recs, root, folder):
    """Library records with `folder` filtered out.

    Needed when intaking from a folder that is itself inside the library: its
    own files would otherwise match themselves as duplicates.
    """
    prefix = os.path.normpath(os.path.join(root, folder)) + os.sep
    return [r for r in recs if not os.path.normpath(r.path).startswith(prefix)]


def gather(sources):
    """Collect audio files as (path, rel) pairs.

    `rel` keeps the folder names the file arrived under, so a download folder
    called 'Tech House' can still hint the genre.
    """
    out = {}
    for s in sources:
        if os.path.isfile(s):
            if os.path.splitext(s)[1].lower() in AUDIO_EXTS:
                out[s] = os.path.basename(s)
        elif os.path.isdir(s):
            base = os.path.dirname(os.path.normpath(s))
            for dirpath, dirnames, filenames in os.walk(s):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in filenames:
                    if fn.startswith("._"):
                        continue
                    if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                        p = os.path.join(dirpath, fn)
                        out[p] = os.path.relpath(p, base)
    return sorted(out.items())


def plan(root, sources, library_recs, progress=None):
    """Return list of dicts: {rec, dest, action, reason}."""
    files = gather(sources)
    # signatures + identities of what is already in the library
    known_sig, known_id = set(), set()
    for r in library_recs:
        known_id.add(ident(r))
        if r.size:
            known_sig.add((r.size, _sig(r.path, r.size)))

    from .library import Rec as _Rec
    from .common import clean_stem, split_artist_title

    results = []
    total = len(files) or 1
    for i, (p, rel) in enumerate(files):
        if progress and i % 10 == 0:
            progress(i, total)
        r = _Rec(path=p, rel=rel, ext=os.path.splitext(p)[1].lower())
        try:
            r.size = os.path.getsize(p)
        except OSError:
            pass
        t = Track(p)
        if t.ok:
            r.artist = (t.get("artist") or "").strip() or None
            r.title = (t.get("title") or "").strip() or None
            r.genre = (t.get("genre") or "").strip() or None
            r.key = (t.get("key") or "").strip() or None
            r.bpm = (t.get("bpm") or "").strip() or None
            r.grouping = (t.get("grouping") or "").strip() or None
            r.comment = (t.get("comment") or "").strip() or None
            r.duration = t.length or 0.0
        if not r.artist or not r.title:
            a, ti = split_artist_title(clean_stem(p))
            r.artist = r.artist or a
            r.title = r.title or ti

        if r.size and (r.size, _sig(p, r.size)) in known_sig:
            results.append({"rec": r, "dest": None, "action": "duplicate",
                            "reason": "identical audio already in library"})
            continue
        if ident(r) in known_id:
            results.append({"rec": r, "dest": None, "action": "duplicate",
                            "reason": "same artist/title already in library"})
            continue

        if r.duration and r.duration >= MIX_MIN_SECONDS:
            dest = os.path.join(root, MIXES_DIR, os.path.basename(p))
            results.append({"rec": r, "dest": dest, "action": "mix",
                            "reason": "longer than 20 minutes"})
        elif not (r.key and r.bpm):
            dest = os.path.join(root, TO_PROCESS, target_filename(r))
            results.append({"rec": r, "dest": dest, "action": "to-process",
                            "reason": "missing key/BPM - run Platinum Notes + Mixed In Key"})
        else:
            g = resolve_genre(r)
            dest = os.path.join(root, TRACKS_DIR, safe(g, 60), target_filename(r))
            results.append({"rec": r, "dest": dest, "action": "sort",
                            "reason": f"genre: {g}"})
    if progress:
        progress(total, total)
    return results


def apply(root, results, move=True, clean_spam=True, log=print, progress=None):
    j = Journal("import", root)
    todo = [x for x in results if x["dest"]]
    total = len(todo) or 1
    n = 0
    restored = []
    for i, x in enumerate(todo):
        if progress and i % 10 == 0:
            progress(i, total)
        src, dest = x["rec"].path, x["dest"]
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            final, stem_ext = dest, os.path.splitext(dest)
            c = 1
            while os.path.exists(final):
                c += 1
                final = f"{stem_ext[0]} ({c}){stem_ext[1]}"
            if move:
                shutil.move(src, final)
                j.moved(src, final)
            else:
                shutil.copy2(src, final)
                j.created(final)
            n += 1
            if clean_spam:
                t = Track(final)
                if t.ok:
                    ch = {}
                    for field in ("genre", "comment"):
                        v = t.get(field)
                        if v and is_spam(v):
                            ch[field] = {"old": v, "new": None}
                            t.set(field, None)
                    if ch and t.save():
                        j.tagged(final, ch)

            # If this track was staged out of a set/vibe folder, put it back
            # into the playlists it came from - now pointing at its new home.
            owed = membership.claim(x["rec"])
            for name in owed:
                pl.append(root, name, [final])
            if owed:
                restored.append(x["rec"])
                log(f"  restored to {len(owed)} playlist(s): {os.path.basename(final)}")
        except Exception as e:
            log(f"  ! {os.path.basename(src)}: {e}")
    if progress:
        progress(total, total)
    if restored:
        membership.release(restored)
        log(f"restored {len(restored)} tracks to their original playlists")
    path = j.save()
    log(f"imported {n} files | journal: {path}")
    return path, n


def summarize(results):
    return collections.Counter(x["action"] for x in results)
