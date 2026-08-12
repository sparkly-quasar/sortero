"""Tag repair: spam removal, artist/title inference, genre normalisation,
and promoting Mixed In Key data out of the comment field into sortable tags."""
import os, re, collections
from .common import is_spam, clean_stem, split_artist_title, parse_mik, to_camelot
from .tagio import Track
from .journal import Journal
from .organize import canon_genre

FIXES = ("energy", "spam", "artist", "genre")
FIX_LABELS = {
    "energy": "Promote Mixed In Key energy to the Grouping field (sortable)",
    "spam": "Strip download-site spam from Genre and Comment",
    "artist": "Fill missing Artist/Title from the filename",
    "genre": "Normalise Genre to a consistent vocabulary",
}


def plan(recs, fixes, log=None):
    """Compute tag changes without writing. Returns list of (rec, {field: (old,new)})."""
    out = []
    for r in recs:
        if r.protected:
            continue
        ch = {}

        # Mixed In Key writes "Cm - Energy 6" into the comment. The key half
        # usually also lands in TKEY, but the energy half is stranded where no
        # DJ app can sort on it. Promote it to Grouping.
        if "energy" in fixes:
            mkey, energy = parse_mik(r.comment)
            if energy is not None:
                cam = to_camelot(r.key or mkey) or (r.key or mkey)
                grouping = f"{cam} - Energy {energy}"
                if (r.grouping or "") != grouping:
                    ch["grouping"] = (r.grouping, grouping)
            if mkey and not r.key:
                ch["key"] = (r.key, mkey)

        if "spam" in fixes:
            if r.genre and is_spam(r.genre):
                ch["genre"] = (r.genre, None)
            if r.comment and is_spam(r.comment):
                ch["comment"] = (r.comment, None)

        if "artist" in fixes:
            a, t = split_artist_title(clean_stem(r.path))
            if not r.artist and a:
                ch["artist"] = (None, a)
            if not r.title and t:
                ch["title"] = (r.title, t)

        if "genre" in fixes:
            cur = ch.get("genre", (r.genre, r.genre))[1] if "genre" in ch else r.genre
            if cur:
                c = canon_genre(cur)
                if c and c != cur:
                    ch["genre"] = (r.genre, c)

        if ch:
            out.append((r, ch))
    return out


def summarize(changes):
    c = collections.Counter()
    for r, ch in changes:
        for field, (old, new) in ch.items():
            c[f"{field}: {'cleared' if new is None else 'set'}"] += 1
    c["files affected"] = len(changes)
    return c


def apply(root, changes, log=print, progress=None):
    j = Journal("fixtags", root)
    total = len(changes) or 1
    failed = 0
    for i, (r, ch) in enumerate(changes):
        if progress and i % 25 == 0:
            progress(i, total)
        t = Track(r.path)
        if not t.ok:
            failed += 1
            continue
        for field, (old, new) in ch.items():
            t.set(field, new)
        if t.save():
            j.tagged(r.path, {f: {"old": o, "new": n} for f, (o, n) in ch.items()})
        else:
            failed += 1
    if progress:
        progress(total, total)
    path = j.save()
    log(f"tagged {len(j.entries)} files ({failed} failed) | journal: {path}")
    return path, len(j.entries), failed
