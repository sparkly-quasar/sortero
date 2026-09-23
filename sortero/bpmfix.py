"""Fix BPMs that are off by a whole ratio: 93.33 for a 140 techno track, 70 for 140.

A track is only questioned when its BPM falls outside the usual range for its
genre - a techno track at 93 - and only changed when the audio's strongest
tempo inside that range sits right on a clean multiple of the current value
(x2, x3/2, x4/3 or the inverse). Anything less clear-cut is left alone: a
wrong "fix" is worse than the original mistake.

The multiple is applied to the existing value rather than replacing it with
the measured tempo, because the analysis tool's number is precise - only
scaled. 93.333 x 3/2 is exactly 140.

Two places hold a BPM: the file's tag, and (if you use it) Mixxx's library,
which ignores the tag once it has analysed a track. Each is judged and fixed
on its own.
"""
import math

from . import mixxx, tempo
from .journal import Journal
from .organize import resolve_genre
from .tagio import Track

# Where a correctly tagged track of each genre sits, in BPM. Deliberately
# generous: a track inside its range is never questioned, so the edges only
# decide what counts as suspicious. Genres whose tempo legitimately wanders
# (downtempo, breaks, hip-hop, disco) aren't listed and are never touched.
RANGES = {
    "Techno (Peak Time)": (118, 160),
    "Techno (Hypnotic)": (115, 160),
    "Techno (Hard)": (125, 175),
    "Techno": (115, 160),
    "Melodic House & Techno": (112, 135),
    "Minimal & Deep Tech": (115, 135),
    "Tech House": (115, 135),
    "Afro House": (112, 130),
    "Progressive House": (112, 135),
    "Deep House": (110, 130),
    "Funky & Soulful House": (112, 132),
    "Bass House": (118, 135),
    "House": (112, 135),
    "Trance": (125, 150),
}

MIN_STRENGTH = 0.3      # below this the audio has no clear pulse to go on
TOLERANCE = 0.012       # how close a multiple must land to the measured tempo
# A slow track with triplet hats also pulses at x3/2 or x4/3 its tempo, so for
# those ratios the audio must clearly prefer the new tempo over the old one.
# Half and double time are different: the audio pulses at both by nature, and
# which one to count is the genre's convention.
MARGIN = 0.05
OCTAVES = (2.0, 0.5)

_backed_up = set()      # Mixxx libraries already backed up this run

RATIO_NAMES = {2.0: "x2", 1.5: "x3/2", 4 / 3: "x4/3",
               0.5: "x1/2", 2 / 3: "x2/3", 0.75: "x3/4"}


class Fix:
    """What to change for one track."""

    def __init__(self, rec, genre, measured):
        self.rec, self.genre, self.measured = rec, genre, measured
        self.tag = None         # (old str, new str, ratio)
        self.mixxx = None       # dict: id, old/new bpm, old/new beats, ratio, locked

    @property
    def ratio(self):
        return self.tag[2] if self.tag else self.mixxx["ratio"]


def genre_range(rec):
    g = resolve_genre(rec, detail="fine")
    return g, RANGES.get(g)


def _num(v):
    try:
        x = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def judge(prof, bpm, lo, hi):
    """The ratio that fixes bpm, or None if it's fine or the audio isn't clear."""
    if not bpm or lo <= bpm <= hi:
        return None
    best = prof.best(lo, hi)
    if prof.score(best) < MIN_STRENGTH:
        return None
    near = [k for k in tempo.RATIOS
            if lo <= bpm * k <= hi and abs(bpm * k - best) <= max(1.0, best * TOLERANCE)]
    if len(near) != 1:
        return None
    k = near[0]
    if k not in OCTAVES and prof.score(bpm * k) < prof.score(bpm) + MARGIN:
        return None
    return k


def new_tag_value(old, ratio, measured):
    """The corrected tag, written the way the old one was.

    A whole-number tag was rounded by whatever wrote it, so the true value
    lies within half a BPM; scaled, that window can straddle two integers
    (93 x 3/2 = 139.5), and the measured tempo picks between them.
    """
    x = _num(old)
    if "." not in str(old):
        lo, hi = (x - 0.5) * ratio, (x + 0.5) * ratio
        return str(int(round(min(max(measured, lo), hi))))
    return f"{x * ratio:.2f}".rstrip("0").rstrip(".")


def _new_grid(prof, t, ratio):
    """Mixxx's grid at the corrected tempo, starting on a real beat.

    Scaling keeps the old grid's first beat, but that isn't always on a beat
    of the true tempo: a 93.33 grid over a 140 track has every other marker
    halfway between two kicks. The true downbeat is among the first few old
    markers, so try each and keep the one the audio agrees with.
    """
    sr, old_bpm = t["samplerate"], t["bpm"]
    new_bpm = old_bpm * ratio
    first = mixxx.grid_first_beat(t["beats"])
    old_period, new_period = 60.0 / old_bpm * sr, 60.0 / new_bpm * sr
    cands = [first + j * old_period for j in range(4)]
    best = max(cands, key=lambda c: prof.on_beat(c / sr, new_bpm))
    start = best - math.floor(best / new_period) * new_period
    return new_bpm, mixxx.grid_blob(t["beats"], new_bpm, int(round(start)))


def verdict(tapped, old, new):
    """Which BPM a tapped tempo agrees with, in words.

    People tap every other kick as often as every kick, so half and double
    of the tapped tempo count as agreeing too.
    """
    def off(target):
        return min(abs(tapped * k - target) / target for k in (0.5, 1.0, 2.0))
    o, n = off(old), off(new)
    if n <= 0.03 and n < o:
        return "matches the fix"
    if o <= 0.03 and o < n:
        return "matches the current BPM: leave this one out"
    return "doesn't clearly match either yet: keep tapping"


def plan(recs, use_mixxx=True, progress=None, log=print):
    """Returns (fixes, stats). Only tracks with an out-of-range BPM are listened to."""
    lib = None
    if use_mixxx:
        db = mixxx.find_db()
        if db:
            lib = mixxx.Library(db)
            log(f"Mixxx library: {db} ({len(lib.by_path):,} tracks)")

    todo = []
    stats = {"checked": 0, "no_genre_range": 0, "unreadable": 0, "unclear": 0,
             "mixxx_skipped": 0}
    for r in recs:
        if r.protected:
            continue
        genre, rng = genre_range(r)
        if not rng:
            stats["no_genre_range"] += 1
            continue
        lo, hi = rng
        tag = _num(r.bpm)
        t = lib.get(r.path) if lib else None
        tag_off = tag and not lo <= tag <= hi
        mx_off = t and t["bpm"] and not lo <= t["bpm"] <= hi
        if tag_off or mx_off:
            todo.append((r, genre, rng, tag if tag_off else None, t if mx_off else None))

    fixes = []
    total = len(todo)
    for i, (r, genre, (lo, hi), tag, t) in enumerate(todo):
        if progress:
            progress(i, total)
        stats["checked"] += 1
        try:
            prof = tempo.profile(r.path, r.duration)
        except Exception:
            stats["unreadable"] += 1
            continue
        f = Fix(r, genre, prof.best(lo, hi))
        k = judge(prof, tag, lo, hi) if tag else None
        if k:
            f.tag = (r.bpm, new_tag_value(r.bpm, k, f.measured), k)
        k = judge(prof, t["bpm"], lo, hi) if t else None
        if k:
            if mixxx.Library.editable(t):
                bpm, blob = _new_grid(prof, t, k)
                f.mixxx = {"db": lib.db, "id": t["id"], "ratio": k,
                           "old_bpm": t["bpm"], "new_bpm": bpm,
                           "old_beats": t["beats"], "new_beats": blob,
                           "locked": t["locked"]}
            else:
                stats["mixxx_skipped"] += 1
        if f.tag or f.mixxx:
            fixes.append(f)
        else:
            stats["unclear"] += 1
    if progress:
        progress(total, total)
    return fixes, stats


def apply(root, fixes, log=print, progress=None):
    """Write the corrected tags, then Mixxx's library in one transaction."""
    mx = [f.mixxx for f in fixes if f.mixxx]
    if mx and mixxx.running():
        raise mixxx.MixxxOpen("Quit Mixxx first. It keeps its library in memory and "
                              "would overwrite these changes when it closes.")
    j = Journal("fix-bpm", root)
    total = len(fixes) or 1
    failed = 0
    for i, f in enumerate(fixes):
        if progress and i % 10 == 0:
            progress(i, total)
        if not f.tag:
            continue
        t = Track(f.rec.path)
        old, new, _ = f.tag
        if t.ok:
            t.set("bpm", new)
        if t.ok and t.save():
            j.tagged(f.rec.path, {"bpm": {"old": old, "new": new}})
            f.rec.bpm = new
        else:
            failed += 1

    if mx:
        db = mx[0]["db"]
        # Once per run: fixing one track at a time shouldn't copy the library each time.
        if db not in _backed_up:
            log(f"Backed up Mixxx's library to {mixxx.backup(db)}")
            _backed_up.add(db)
        # Lock the corrected BPM, or Mixxx's next re-analysis puts the wrong one back.
        mixxx.write(db, [(m["id"], m["new_bpm"], m["new_beats"], True) for m in mx])
        for m in mx:
            j.mixxx(db, m["id"],
                    old={"bpm": m["old_bpm"], "beats": m["old_beats"].hex(),
                         "locked": m["locked"]},
                    new={"bpm": m["new_bpm"], "beats": m["new_beats"].hex(),
                         "locked": True})
    if progress:
        progress(total, total)
    path = j.save()
    log(f"fixed BPM: {sum(1 for f in fixes if f.tag) - failed} tags "
        f"({failed} failed), {len(mx)} in Mixxx | journal: {path}")
    return path, failed
