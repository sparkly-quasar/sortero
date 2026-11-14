"""What to play next, and what order a set flows best in, from tags alone.

Every track already carries what a DJ checks before a blend: its key, its
tempo and, from Mixed In Key, an energy rating. This module turns those into a
score for how well one track follows another, with the reasons in plain words,
so a suggestion can always say why it's there.

Three things feed the score:

- **Key**, on the Camelot wheel: the same key, a step either way, or the
  relative major/minor mix cleanly; a few other moves work with care; the rest
  clash.
- **Tempo**: close tempos blend with a nudge of the pitch fader; half and
  double time count as close. Past TEMPO_LIMIT apart, it's a jump.
- **Energy**: the change from one track to the next, judged against where you
  want the set to go: build, hold, drop, or anywhere smooth.

On top of that, a History of your own playlists: tracks you've put next to
each other before get a lift. Your gig sets are the best evidence there is of
what you think works, and the play history a player writes to a stick can be
added the same way.

The weights are judgments, not measurements. They're constants here so they
can be tuned against real sets: hold out a set you played and see how often
the track you actually played next lands in the top suggestions.

Pure Python, like tempo.py: no numpy, so the universal Mac build stays simple.
"""
import collections, math, os, re
from dataclasses import dataclass, field

from .common import fold

# How much each part counts towards the score. They add up to 1.
KEY_WEIGHT = 0.45
TEMPO_WEIGHT = 0.35
ENERGY_WEIGHT = 0.20
# Added on top for a pair your playlists put next to each other, up to this.
HISTORY_BONUS = 0.15
# A part we can't judge (no key, no BPM, no energy) scores this: no help, no harm.
NEUTRAL = 0.5

# Within TEMPO_EASY of each other, the pitch fader barely moves. Past
# TEMPO_LIMIT, a blend needs a tempo ride, not a nudge: around 6-8% is also
# where key lock starts to sound processed.
TEMPO_EASY = 0.015
TEMPO_LIMIT = 0.08

DIRECTIONS = ("build", "hold", "drop")

SHAPES = {
    "smooth": "Smoothest",
    "build": "Build up",
    "peak": "Build, peak, land",
    "waves": "Waves",
}
# How much keeping to the shape counts against a smooth blend, when ordering.
SHAPE_WEIGHT = 0.6

_WHEEL = re.compile(r"\s*(1[0-2]|[1-9])\s*([AB])\s*", re.I)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


# ------------------------------------------------------------------ reading
def wheel(code):
    """A Camelot code as (number, letter): "8a" -> (8, "A"). None if it isn't one."""
    m = _WHEEL.fullmatch(code or "")
    return (int(m.group(1)), m.group(2).upper()) if m else None


def bpm_of(rec):
    """A track's BPM as a number, or None. Tags hold "128", "127.98", "128,00"."""
    m = _NUMBER.search(str(rec.bpm or ""))
    if not m:
        return None
    v = float(m.group().replace(",", "."))
    return v if 40 <= v <= 300 else None


def _norm(path):
    return os.path.normcase(os.path.normpath(path))


def _song(rec):
    """The same recording under another file: same artist, same title."""
    if rec.artist and rec.title:
        return fold(rec.artist), fold(rec.title)
    return _norm(rec.path)


# ------------------------------------------------------------------ parts
def key_fit(a, b):
    """How well Camelot key b follows a: (score 0-1, reason)."""
    wa, wb = wheel(a), wheel(b)
    if not wa or not wb:
        return NEUTRAL, None
    (na, la), (nb, lb) = wa, wb
    step = (nb - na) % 12           # steps clockwise round the wheel
    same = la == lb
    move = f"{na}{la} → {nb}{lb}"
    if same and step == 0:
        return 1.0, "Same key"
    if same and step in (1, 11):
        return 0.9, f"Next on the wheel ({move})"
    if not same and step == 0:
        return 0.85, f"Relative {'major' if lb == 'B' else 'minor'} ({move})"
    # One step round and across: still only one note apart, but the mood turns.
    if not same and step in (1, 11):
        return 0.65, f"Diagonal on the wheel ({move})"
    if same and step == 2:
        return 0.55, f"Two steps up, a lift ({move})"
    if same and step == 10:
        return 0.45, f"Two steps down ({move})"
    # Seven steps round the wheel is a semitone up: the classic key-change lift.
    if same and step == 7:
        return 0.5, f"A semitone up, a lift ({move})"
    return 0.0, f"Key clash ({move})"


def tempo_fit(a, b):
    """How well BPM b follows a: (score 0-1, reason). Half and double time count."""
    if not a or not b:
        return NEUTRAL, None
    best = None
    for ratio, how in ((1.0, ""), (2.0, ", half time"), (0.5, ", double time")):
        diff = (b * ratio - a) / a
        if best is None or abs(diff) < abs(best[0]) - 1e-9:
            best = (diff, how)
    diff, how = best
    pct = abs(diff)
    if pct <= TEMPO_EASY:
        return 1.0, f"Same tempo{how}"
    if pct <= TEMPO_LIMIT:
        score = 1.0 - 0.8 * (pct - TEMPO_EASY) / (TEMPO_LIMIT - TEMPO_EASY)
        return score, f"{'+' if diff > 0 else '-'}{pct * 100:.0f}% tempo{how}"
    return 0.0, f"Tempo jump ({a:g} → {b:g} BPM)"


# For each direction, the score for each change in energy (b - a).
_ENERGY = {
    "build": {1: 1.0, 2: 0.9, 0: 0.6, 3: 0.5},
    "hold": {0: 1.0, 1: 0.7, -1: 0.7, 2: 0.3, -2: 0.3},
    "drop": {-1: 1.0, -2: 0.9, 0: 0.6, -3: 0.5},
    None: {0: 1.0, 1: 1.0, -1: 1.0, 2: 0.6, -2: 0.6, 3: 0.3, -3: 0.3},
}


def energy_fit(a, b, direction=None):
    """How well energy b follows a, going `direction`: (score 0-1, reason)."""
    if a is None or b is None:
        return NEUTRAL, None
    d = b - a
    score = _ENERGY[direction].get(d)
    if score is None:
        going_wrong_way = (direction == "build" and d < 0) or (direction == "drop" and d > 0)
        score = 0.1 if going_wrong_way or direction == "hold" else 0.15
    return score, f"Energy {a} → {b}"


# ------------------------------------------------------------------ history
class History:
    """Which tracks you've put next to each other, and how often.

    Fed with sequences of file paths in order: your playlists, and later the
    play history from a stick. A heavier weight counts a sequence for more,
    so a set you actually played can outweigh a playlist you only collected.
    """

    def __init__(self):
        self._after = collections.Counter()

    def add(self, paths, weight=1):
        keys = [_norm(p) for p in paths]
        for x, y in zip(keys, keys[1:]):
            if x != y:
                self._after[(x, y)] += weight
        return self

    def times(self, a, b):
        """(times b came straight after a, times a came straight after b)."""
        x, y = _norm(a), _norm(b)
        return self._after[(x, y)], self._after[(y, x)]

    def __len__(self):
        return len(self._after)

    @classmethod
    def from_playlists(cls, root):
        """Every playlist in the collection's _Playlists folder."""
        from . import playlists
        h = cls()
        for name in playlists.existing(root):
            try:
                h.add(playlists.entries(root, name))
            except (OSError, UnicodeDecodeError):
                continue
        return h


def _history_fit(a, b, history):
    if history is None:
        return 0.0, 0, None
    after, before = history.times(a.path, b.path)
    if not after and not before:
        return 0.0, 0, None
    # Straight after counts fully; the other way round, half.
    evidence = after + 0.5 * before
    bonus = HISTORY_BONUS * min(1.0, evidence / 2)
    if after:
        why = "You've played it next before" if after == 1 else \
            f"You've played it next {after:g} times"
    else:
        why = "You've paired them before, the other way round"
    return bonus, after + before, why


# ------------------------------------------------------------------ scoring
@dataclass
class Fit:
    """How well one track follows another."""
    score: float
    key: float
    tempo: float
    energy: float
    together: float = 0      # times your playlists put them next to each other
    reasons: list = field(default_factory=list)

    @property
    def clash(self):
        """A key clash or a tempo jump, with no history to vouch for it."""
        return (self.key == 0 or self.tempo == 0) and not self.together


def fit(a, b, direction=None, history=None):
    """How well track b follows track a, going `direction` (build/hold/drop/None)."""
    if direction not in _ENERGY:
        raise ValueError(f"direction must be one of {DIRECTIONS} or None")
    k, kwhy = key_fit(a.camelot, b.camelot)
    t, twhy = tempo_fit(bpm_of(a), bpm_of(b))
    e, ewhy = energy_fit(a.energy, b.energy, direction)
    bonus, together, hwhy = _history_fit(a, b, history)
    score = KEY_WEIGHT * k + TEMPO_WEIGHT * t + ENERGY_WEIGHT * e + bonus
    reasons = [w for w in (hwhy, kwhy, twhy, ewhy) if w]
    return Fit(score=min(1.0, score), key=k, tempo=t, energy=e,
               together=together, reasons=reasons)


def suggest(current, recs, direction=None, history=None, exclude=(), n=9,
            clashes=False):
    """The n tracks that follow `current` best, as [(rec, Fit)], best first.

    Leaves out `current` itself, other copies of the same song, set recordings,
    anything in `exclude` (the rest of the set, say) and, unless `clashes`, key
    clashes and tempo jumps you haven't paired before.
    """
    skip = {_norm(current.path)} | {_norm(getattr(p, "path", p)) for p in exclude}
    me = _song(current)
    out = []
    for r in recs:
        if r.is_recording or _norm(r.path) in skip or _song(r) == me:
            continue
        f = fit(current, r, direction, history)
        if f.clash and not clashes:
            continue
        out.append((r, f))
    out.sort(key=lambda rf: (-rf[1].score, rf[0].display.casefold()))
    return out[:n]


# ------------------------------------------------------------------ ordering
def shape(name, n, lo, hi):
    """Target energy for each of n places in a set, between lo and hi.

    None for "smooth", which has no shape: only the blends count.
    """
    if name not in SHAPES:
        raise ValueError(f"shape must be one of {tuple(SHAPES)}")
    if name == "smooth" or n < 2:
        return None
    out = []
    for i in range(n):
        t = i / (n - 1)
        if name == "build":
            u = t
        elif name == "peak":
            # Up to the top three quarters of the way in, then land halfway down.
            u = t / 0.75 if t <= 0.75 else 1.0 - 0.5 * (t - 0.75) / 0.25
        else:   # waves: climbing overall, with two swells and a breather after each
            u = 0.7 * t + 0.3 * (0.5 - 0.5 * math.cos(4 * math.pi * t))
        out.append(lo + (hi - lo) * u)
    return out


def order(recs, shape_name="smooth", start=None, history=None, rounds=20):
    """Put a set's tracks in the order that flows best. Returns a new list.

    Blends count most; with a shape other than "smooth", so does keeping each
    track's energy near where the shape wants it. `start` pins the opener.
    """
    tracks = list(recs)
    n = len(tracks)
    if n < 2:
        return tracks
    energies = [r.energy for r in tracks if r.energy is not None]
    lo, hi = (min(energies), max(energies)) if energies else (0, 0)
    targets = shape(shape_name, n, lo, hi) if hi > lo else None

    # 1 - fit for every ordered pair; the energy direction is left open here,
    # because the shape says where energy should go.
    blend = [[0.0 if i == j else 1.0 - fit(a, b, None, history).score
              for j, b in enumerate(tracks)] for i, a in enumerate(tracks)]
    span = max(1, hi - lo)

    def off_shape(i, pos):
        if targets is None:
            return 0.0
        e = tracks[i].energy
        return 0.3 if e is None else abs(e - targets[pos]) / span

    place = [[SHAPE_WEIGHT * off_shape(i, pos) for pos in range(n)] for i in range(n)]

    def cost(seq):
        return (sum(blend[x][y] for x, y in zip(seq, seq[1:]))
                + sum(place[x][pos] for pos, x in enumerate(seq)))

    first = None
    if start is not None:
        sp = _norm(getattr(start, "path", start))
        first = next((i for i, r in enumerate(tracks) if _norm(r.path) == sp), None)
    pinned = first is not None
    if first is None:
        # Open with whatever sits best in the first place: on a shape, the
        # track nearest its starting energy; on smooth, the calmest track.
        first = min(range(n), key=lambda i: (place[i][0],
                                             tracks[i].energy if tracks[i].energy is not None else 99,
                                             tracks[i].display.casefold()))

    # Greedy: each next track is the best blend from the last one, in its place.
    seq, left = [first], set(range(n)) - {first}
    while left:
        last, pos = seq[-1], len(seq)
        nxt = min(left, key=lambda j: (blend[last][j] + place[j][pos],
                                       tracks[j].display.casefold()))
        seq.append(nxt)
        left.remove(nxt)

    # Then tidy: move single tracks and swap pairs while it lowers the cost.
    best = cost(seq)
    lo_pos = 1 if pinned else 0
    for _ in range(rounds):
        improved = False
        for i in range(lo_pos, n):
            for j in range(lo_pos, n):
                if i == j:
                    continue
                for trial in (_moved(seq, i, j), _swapped(seq, i, j) if i < j else None):
                    if trial is None:
                        continue
                    c = cost(trial)
                    if c < best - 1e-9:
                        seq, best, improved = trial, c, True
        if not improved:
            break
    return [tracks[i] for i in seq]


def _moved(seq, i, j):
    s = list(seq)
    s.insert(j, s.pop(i))
    return s


def _swapped(seq, i, j):
    s = list(seq)
    s[i], s[j] = s[j], s[i]
    return s


def transitions(seq, history=None):
    """The Fit of each blend in an ordered set: len(seq) - 1 of them.

    For showing a set with the weak links marked.
    """
    return [fit(a, b, _direction(a, b), history) for a, b in zip(seq, seq[1:])]


def _direction(a, b):
    """The direction a set actually goes between two tracks, for judging it."""
    if a.energy is None or b.energy is None:
        return None
    d = b.energy - a.energy
    return "build" if d > 0 else "drop" if d < 0 else "hold"
