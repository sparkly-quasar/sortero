"""Tag repair: spam removal, artist/title inference, genre normalisation,
and promoting Mixed In Key data out of the comment field into sortable tags."""
import os, re, collections, unicodedata
from .common import (ARTIST_TITLE, is_spam, clean_stem, fold, split_artist_title,
                     parse_mik, to_camelot)
from .tagio import Track
from .journal import Journal
from .organize import canon_genre, _strip_artist_prefix

FIXES = ("energy", "spam", "artist", "repeat", "spelling", "genre")
FIX_LABELS = {
    "energy": "Copy Mixed In Key's key and energy (like '8A - Energy 6') into the "
              "Grouping tag, a column DJ apps can show and sort by",
    "spam": "Clear download-site spam from Genre and Comment",
    "artist": "Fill in a missing artist or title from the filename",
    "repeat": "Take the artist out of the title ('Nora En Pure - Sphinx' becomes "
              "'Sphinx'), filling in a missing or partial artist from it",
    "spelling": "Spell each artist the same way everywhere "
                "('Christopher Schwarzwalder' becomes 'Christopher Schwarzwälder')",
    "genre": "Tidy genre names into one consistent set",
}

# The same, short enough for a tick box in the setup guide
FIX_SHORT = {
    "energy": "Copy Mixed In Key's key and energy into Grouping",
    "spam": "Clear download-site spam",
    "artist": "Fill in missing artists and titles",
    "repeat": "Take the artist out of the title",
    "spelling": "Spell each artist the same way",
    "genre": "Tidy genre names into one set",
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


def repeats_artist(r):
    """The title without the artist's name in front, when the tagged title
    starts with it ("Nora En Pure - Sphinx" by Nora En Pure); otherwise None.

    Only an exact match counts, ignoring case, accents and punctuation, so a
    title like "Ipanema - Extended Mix" is never touched.
    """
    a, t = tagged_names(r)
    if not a or not t:
        return None
    stripped = _strip_artist_prefix(t, a)
    return stripped if stripped != t else None


# Between the names in an artist tag: "A, B & C feat. D". Kept when splitting,
# so a tag is put back together exactly as it was apart from the names.
NAME_SEP = re.compile(r"(\s*(?:,|&|/|;|\bfeat\.?|\bft\.?|\bfeaturing\b|\bvs\.?|"
                      r"\bx\b|\band\b)\s*)", re.I)


def _plain(name):
    """A name with case, accents, spacing and punctuation taken out."""
    s = unicodedata.normalize("NFKD", name.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s)


PLACEHOLDER = re.compile(r"(?i)^\W*(unknown( artist)?|various( artists)?|va)\W*$")


def _core(part):
    """One name out of an artist tag, without a bracket that belongs to the
    tag around it: in "John Plaza (Remixes by A and B)" the ")" is not B's.
    A name's own brackets, like "Mira (Berlin)", stay."""
    name = part.strip()
    while name.endswith((")", "]")) and name.count(")") + name.count("]") > \
            name.count("(") + name.count("["):
        name = name[:-1].rstrip()
    while name.startswith(("(", "[")) and name.count("(") + name.count("[") > \
            name.count(")") + name.count("]"):
        name = name[1:].lstrip()
    return name


def _counts(name):
    """Is this a name worth comparing? Not a placeholder, and long enough."""
    return len(_plain(name)) >= 3 and not PLACEHOLDER.match(name)


def _same_text(a, b):
    """Spelled identically, differing only in how accented letters are encoded."""
    return a != b and unicodedata.normalize("NFC", a) == unicodedata.normalize("NFC", b)


def _accents(name):
    return sum(1 for c in unicodedata.normalize("NFKD", name) if unicodedata.combining(c))


# What title_credit finds in a title shaped "Artist - Title":
REPEAT = "repeat"     # the tagged artist, again: "Nora En Pure - Sphinx"
MORE = "more"         # the tagged artist and others: "Djuma Soundsystem & Westerby - ..."
MISSING = "missing"   # no artist tag, and the filename names the same artist
OTHER = "other"       # someone else, and the filename agrees with the title
SAFE = (REPEAT, MORE, MISSING)  # the Clean tags fix; OTHER is for you to pick


def _names(credit):
    return {_plain(n) for n in NAME_SEP.split(credit or "")[::2] if _plain(n)}


def title_credit(r, order=ARTIST_TITLE):
    """(kind, artist, title) when the title tag starts with an artist credit,
    or None. `artist` is what the artist tag should say, or None to keep it.

    A title like "Ipanema - Extended Mix" is never matched: its first half is
    neither the tagged artist nor what the filename names as the artist. A
    title written the other way round ("OH OHH - Deborah De Luca") isn't
    either, since the tagged artist is in its second half.
    """
    a, t = tagged_names(r)
    if not t:
        return None
    if a:
        stripped = _strip_artist_prefix(t, a)
        if stripped != t:
            return REPEAT, None, stripped
    head = tail = None
    for sep in (" - ", " – ", " — "):
        h, found, tl = t.partition(sep)
        if found and h.strip() and tl.strip():
            head, tail = h.strip(), tl.strip()
            break
    if not head:
        return None
    if a and _names(a) < _names(head):
        return MORE, head, tail
    file_artist, _ = split_artist_title(clean_stem(r.path), order)
    if not file_artist or _plain(file_artist) != _plain(head):
        return None
    if not a:
        return MISSING, head, tail
    if _names(a) & _names(tail) or _plain(a) in _plain(tail):
        return None                     # "Song - Artist": the tags are right
    return OTHER, head, tail


def artist_in_title(r, order=ARTIST_TITLE):
    """Is this one the Clean tags fix sorts out by itself?"""
    got = title_credit(r, order)
    return bool(got) and got[0] in SAFE


def names_other_artist(r, order=ARTIST_TITLE):
    """Does the title name a different artist from the tag, with the filename
    backing it up? Those are listed for you to pick from, not fixed in bulk,
    leaving out the ones you said are right as they are."""
    if _left_alone_key(r) in _left_alone():
        return False
    got = title_credit(r, order)
    return bool(got) and got[0] == OTHER


# Tracks whose title names another artist on purpose, a remix credited to its
# original artist say, so they stop being flagged. By filename, like
# library.genre_ignored, since filing moves a track but keeps its name.
LEFT_ALONE = "title_artist_ignored"
_left_alone_cache = None


def _left_alone_key(r):
    return os.path.basename(r.path).lower()


def _left_alone():
    global _left_alone_cache
    if _left_alone_cache is None:
        from . import settings
        _left_alone_cache = set(settings.get(LEFT_ALONE) or [])
    return _left_alone_cache


def leave_title_artist(recs, on=True):
    """Stop (or start again) flagging these tracks' titles as naming another artist."""
    global _left_alone_cache
    from . import settings
    keys = {_left_alone_key(r) for r in recs}
    _left_alone_cache = (_left_alone() | keys) if on else (_left_alone() - keys)
    settings.set(LEFT_ALONE, sorted(_left_alone_cache))


def credit_from_title(recs, order=ARTIST_TITLE):
    """Planned changes taking artist and title from titles shaped "Artist -
    Title", for tracks you picked (any kind, OTHER included)."""
    out = []
    for r in recs:
        if r.protected:
            continue
        got = title_credit(r, order)
        if not got:
            continue
        kind, artist, title = got
        ch = {"title": (r.title, title)}
        if artist:
            ch["artist"] = (tagged_names(r)[0], artist)
        out.append((r, ch))
    return out


def guess_spelling(spellings):
    """Sortero's pick among the spellings one artist is tagged with (a Counter).

    The accented spelling wins, since accents get lost far more often than
    added ("Schwarzwalder" for "Schwarzwälder"). Then the commoner one, then
    one that isn't all capitals or all lower case.
    """
    return max(spellings, key=lambda n: (_accents(n), spellings[n], not n.isupper(),
                                         not n.islower(), n))


# What the user decided, by plain name: the spelling to use, or LEAVE.
CHOICES = "artist_spellings"
LEAVE = ""


def spelling_choices():
    from . import settings
    got = settings.get(CHOICES)
    return dict(got) if isinstance(got, dict) else {}


def choose_spelling(key, spelling):
    """Remember a spelling for one artist (by plain name), LEAVE to keep its
    tags as they are, or None to go back to Sortero's guess."""
    from . import settings
    choices = spelling_choices()
    if spelling is None:
        choices.pop(key, None)
    else:
        choices[key] = unicodedata.normalize("NFC", spelling.strip())
    settings.set(CHOICES, choices)


def artist_spellings(recs, choices=None):
    """{plain name: the spelling to use}, each name in "A, B & C" on its own.

    Sortero's guess (guess_spelling), unless the user chose a spelling or said
    to leave an artist alone. Spellings that differ only in how an accented
    letter is encoded count as one, written the standard (NFC) way, so those
    get tidied too.
    """
    choices = spelling_choices() if choices is None else choices
    out = {}
    for key, sp in _spellings_seen(recs).items():
        chosen = choices.get(key)
        if chosen == LEAVE:
            continue
        out[key] = chosen or guess_spelling(sp)
    return out


def _spellings_seen(recs):
    """{plain name: Counter of the spellings tags use for it}."""
    seen = collections.defaultdict(collections.Counter)
    for r in recs:
        if r.protected:
            continue
        a, _ = tagged_names(r)
        for part in NAME_SEP.split(a or "")[::2]:
            name = unicodedata.normalize("NFC", _core(part))
            if _counts(name):
                seen[_plain(name)][name] += 1
    return seen


def spelling_review(recs, choices=None):
    """Every artist tagged more than one way, for the user to check, most-used
    first: [{"key", "spellings" (Counter), "guess", "chosen" (their spelling,
    LEAVE, or None)}]. Encoding-only differences aren't listed: nobody can see
    them, though the fix still tidies them."""
    choices = spelling_choices() if choices is None else choices
    seen = _spellings_seen(recs)
    rows = [{"key": k, "spellings": sp, "guess": guess_spelling(sp),
             "chosen": choices.get(k)}
            for k, sp in seen.items() if len(sp) > 1]
    return sorted(rows, key=lambda x: (-sum(x["spellings"].values()), x["key"]))


def spelled_differently(recs, choices=None):
    """[(spelling to use, [other spellings])] for each artist still tagged more
    than one way, most-used first, leaving out artists the user said to leave."""
    out = []
    for row in spelling_review(recs, choices):
        if row["chosen"] == LEAVE:
            continue
        best = row["chosen"] or row["guess"]
        out.append((best, sorted(n for n in row["spellings"] if n != best)))
    return out


def respelled(artist, spellings):
    """The artist tag with each name spelled the collection's way, or None
    when nothing about it changes."""
    if not artist:
        return None
    parts = NAME_SEP.split(artist)
    for i in range(0, len(parts), 2):
        name = _core(parts[i])
        best = spellings.get(_plain(name)) if _counts(name) else None
        if best and best != name:
            parts[i] = parts[i].replace(name, best)
    new = "".join(parts)
    return new if new != artist else None


def plan(recs, fixes, log=None, order=ARTIST_TITLE):
    """Compute tag changes without writing. Returns list of (rec, {field: (old,new)}).

    `order` is how a "A - B" filename is read when filling in a missing name.
    """
    out = []
    spellings = artist_spellings(recs) if "spelling" in fixes else {}
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

        if "spelling" in fixes and "artist" not in ch:
            new = respelled(tagged_names(r)[0], spellings)
            if new:
                ch["artist"] = (r.artist, new)

        if "repeat" in fixes and "title" not in ch:
            got = title_credit(r, order)
            if got and got[0] in SAFE:
                kind, artist, title = got
                # the filename fix may have filled the artist in already: fine
                # when it's the same name, but not when it says someone else
                if not (artist and "artist" in ch
                        and _plain(ch["artist"][1]) != _plain(artist)):
                    ch["title"] = (r.title, title)
                    if artist:
                        ch["artist"] = (tagged_names(r)[0], artist)

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


def _short(v, n=48):
    v = str(v)
    return v if len(v) <= n else v[:n - 1] + "…"


def overview_lines(changes):
    """What a clean-up does, one line per kind of change, largest first.

    The name-based example lines only fit jobs that change names. For a clean-up
    that clears a genre, they show the same name either side of 'becomes', which
    reads as nothing happening.
    """
    c = collections.Counter()
    for r, ch in changes:
        for field, (old, new) in ch.items():
            how = "clear" if new is None else "fill" if old in (None, "") else "change"
            c[(field, how)] += 1
    verbs = {"clear": "Clear download-site text from {} on {}",
             "fill": "Fill in {} on {}", "change": "Change {} on {}"}
    out = []
    for (field, how), k in c.most_common():
        files = f"{k:,} file" + ("" if k == 1 else "s")
        out.append("• " + verbs[how].format(field_label(field), files))
    return out


def change_lines(changes, n=3):
    """A few real changes, each saying which tag goes from what to what."""
    out = []
    for r, ch in changes[:n]:
        name = (f"{r.artist} - {r.title}" if r.artist and r.title
                else os.path.basename(r.path))
        parts = []
        for field, (old, new) in ch.items():
            label = field_label(field)
            if new is None:
                parts.append(f"{label} '{_short(old)}' is cleared")
            elif old in (None, ""):
                parts.append(f"{label} is set to '{_short(new)}'")
            else:
                parts.append(f"{label} '{_short(old)}' becomes '{_short(new)}'")
        out.append(f"{_short(name, 60)}: " + "; ".join(parts))
    return out


def preview_rows(changes, limit=2000):
    """(file, tag, now, becomes) for each planned change, as a table shows them."""
    out = []
    for r, ch in changes:
        for field, (old, new) in ch.items():
            if len(out) >= limit:
                return out
            out.append((
                os.path.basename(r.path), field_label(field),
                # a blank cell reads as missing data; say the tag is empty now
                "(empty)" if old in (None, "") else str(old)[:120],
                "(cleared)" if new is None else
                # looks the same, but an accent was stored a different way
                str(new)[:100] + "  (accents re-encoded)"
                if _same_text(old or "", new) else str(new)[:120]))
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
