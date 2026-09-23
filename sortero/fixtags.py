"""Tag repair: spam removal, artist/title inference, genre normalisation,
and promoting Mixed In Key data out of the comment field into sortable tags."""
import os, re, collections
from .common import (ARTIST_TITLE, is_spam, clean_stem, fold, split_artist_title,
                     parse_mik, to_camelot)
from .tagio import Track
from .journal import Journal
from .organize import canon_genre

FIXES = ("energy", "spam", "artist", "genre")
FIX_LABELS = {
    "energy": "Copy Mixed In Key's key and energy (like '8A - Energy 6') into the "
              "Grouping tag, a column DJ apps can show and sort by",
    "spam": "Clear download-site spam from Genre and Comment",
    "artist": "Fill in a missing artist or title from the filename",
    "genre": "Tidy genre names into one consistent set",
}

# What each tag is called on screen. "grouping" alone means nothing to most
# people; what Sortero puts there is the key and energy.
FIELD_LABELS = {
    "grouping": "Key + energy (Grouping)", "key": "Key", "genre": "Genre",
    "comment": "Comment", "artist": "Artist", "title": "Title", "bpm": "BPM",
    "album": "Album",
}


def field_label(field):
    return FIELD_LABELS.get(field, field.capitalize())


def tagged_names(r):
    """The artist and title actually written to the file.

    A scan fills a missing name in from the filename so the rest of the app has
    something to show, but nothing is on disk yet. Tag repair has to know the
    difference: that guess is what these fixes are here to write, not something
    already written.
    """
    return ((None if r.artist_from_name else r.artist) or None,
            (None if r.title_from_name else r.title) or None)


def plan(recs, fixes, log=None, order=ARTIST_TITLE):
    """Compute tag changes without writing. Returns list of (rec, {field: (old,new)}).

    `order` is how a "A - B" filename is read when filling in a missing name.
    """
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
            a, t = split_artist_title(clean_stem(r.path), order)
            cur_a, cur_t = tagged_names(r)
            if not cur_a and a:
                ch["artist"] = (None, a)
            if not cur_t and t:
                ch["title"] = (None, t)

        if "genre" in fixes:
            cur = ch.get("genre", (r.genre, r.genre))[1] if "genre" in ch else r.genre
            if cur:
                c = canon_genre(cur)
                if c and c != cur:
                    ch["genre"] = (r.genre, c)

        if ch:
            out.append((r, ch))
    return out


def swap(recs):
    """Plan a straight artist <-> title swap, for tags that went in backwards.

    A track with only one of the two set still swaps: the whole "Title - Artist"
    string sitting in the artist field is exactly the mess this fixes. A track
    with no artist or title tag at all is left alone - there is nothing written
    to put the wrong way round, and swapping a name Sortero itself read off the
    filename would only undo a correct reading.
    """
    out = []
    for r in recs:
        if r.protected:
            continue
        a, t = tagged_names(r)
        if not (a or t) or fold(a) == fold(t):
            continue
        out.append((r, {"artist": (a, t), "title": (t, a)}))
    return out


def from_filename(recs, order=ARTIST_TITLE):
    """Plan re-reading artist and title off the filename in the given order.

    Unlike the "artist" fix this overwrites names that are already there, which
    is the point: it's for a batch whose tags were filled in the wrong order.
    Files with nothing to split on are left alone.
    """
    out = []
    for r in recs:
        if r.protected:
            continue
        a, t = split_artist_title(clean_stem(r.path), order)
        if not a:
            continue
        cur_a, cur_t = tagged_names(r)
        ch = {}
        if fold(cur_a) != fold(a):
            ch["artist"] = (cur_a, a)
        if fold(cur_t) != fold(t):
            ch["title"] = (cur_t, t)
        if ch:
            out.append((r, ch))
    return out


def example_lines(changes, n=3):
    """'Artist - Title  becomes  Title - Artist', for the first few changes.

    A few rows of someone's own music do more to stop a wrong write than any
    amount of warning prose: they can recognise the mistake at a glance instead
    of having to assess a description of it.
    """
    out = []
    for r, ch in changes[:n]:
        a = ch.get("artist", (r.artist, r.artist))
        t = ch.get("title", (r.title, r.title))
        out.append(f"{a[0] or '—'} - {t[0] or '—'}      becomes      "
                   f"{a[1] or '—'} - {t[1] or '—'}")
    return out


def summarize(changes):
    c = collections.Counter()
    for r, ch in changes:
        for field, (old, new) in ch.items():
            c[f"{field_label(field)}: {'cleared' if new is None else 'set'}"] += 1
    c["files affected"] = len(changes)
    return c


def apply(root, changes, log=print, progress=None, kind="fixtags"):
    """Write a plan out. `kind` names the batch in History, so an undo is easy
    to find later."""
    j = Journal(kind, root)
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
