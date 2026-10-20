"""The Gig prep checklist: which steps are done, which comes next, and what to say.

Kept apart from the screen so it can be tested without a window. The screen
hands over what it knows (State) and draws whatever steps() returns.

Five steps, in the order a gig is prepared:

  set     Your set: the playlists, in play order
  tracks  Make the tracks gig-safe: check every track, copy the ones that won't play
  main    Main stick: Sortero writes the set to it for Pioneer players (usbexport),
          or rekordbox / Engine DJ do from the playlists file; then it's checked
  ready   Ready to leave: GIG READY once the main stick passes
  backup  Backup stick (optional): numbered folders on a second stick, checked

A step is DONE, NEXT (the one to do now) or WAITING (not yet). The backup is
optional, so it comes last: it can be done any time after the tracks, and is
NEXT once the main stick passes, but Ready to leave is what opens then.
"""
import time
from dataclasses import dataclass, field

from . import convert

DONE, NEXT, WAITING = "done", "next", "waiting"
MARKS = {DONE: "✓", NEXT: "→", WAITING: "○"}
KEYS = ("set", "tracks", "main", "ready", "backup")

# Bytes per second of audio, for the space a copy will take.
RATE_BYTES = {convert.MP3: 320_000 // 8, convert.AIFF: 44_100 * 2 * 2}


@dataclass
class StickCheck:
    """What a stick check found, kept short enough to remember across restarts."""
    name: str                   # the stick's volume name
    when: float                 # time.time() of the check
    ready: bool
    verdict: str                # e.g. "Not ready yet: 1 thing to fix."
    summary: str = ""           # e.g. "rekordbox library, 10 tracks"
    first_problem: str = ""

    def to_dict(self):
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class State:
    playlists: list = field(default_factory=list)   # chosen, in set order
    track_count: int = 0
    apps: str = "rekordbox"     # where the main stick's library comes from, for wording
    direct: bool = False        # Sortero writes the main stick itself (Pioneer players chosen)
    items: list = None          # convert.plan's result, once the tracks are checked
    copied: bool = False        # convert.run has finished on these items
    sets: list = None           # [(playlist, [paths])] as read when the tracks were checked
    xml_path: str = ""          # the rekordbox XML, once made
    xml_cues: dict = field(default_factory=dict)    # cues.tally: {"cues"/"grids": {source: n}}
    xml_tracks: int = 0         # tracks those counts are out of
    main: StickCheck = None     # the main stick's last check
    backup: object = None       # the last backup.Result, if a backup stick was written
    backup_check: StickCheck = None
    earlier: float = None       # sticks checked in an earlier session, for this same set


@dataclass
class Step:
    key: str
    title: str
    text: str
    status: str
    button: str = None


COPIED = (convert.CONVERT, convert.CONVERTED, convert.REUSED)


def estimate_bytes(items, statuses=(convert.CONVERT,)):
    """Roughly how much space the copies take: those still to make, by default."""
    return int(sum((it.info.extras.get("duration") or 0) * RATE_BYTES[it.target]
                   for it in items if it.status in statuses and it.info))


def _size(n):
    from ..common import human_size
    return human_size(n)


def _plural(n, one, many=None):
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def when(t, now=None):
    """"21:14" today, "yesterday 21:14", or "14 Oct 21:14"."""
    now = time.localtime(now if now is not None else time.time())
    then = time.localtime(t)
    clock = time.strftime("%H:%M", then)
    days = (time.mktime(now[:3] + (0, 0, 0, 0, 0, -1))
            - time.mktime(then[:3] + (0, 0, 0, 0, 0, -1))) / 86400
    if round(days) == 0:
        return clock
    if round(days) == 1:
        return f"yesterday {clock}"
    return f"{then.tm_mday} {time.strftime('%b', then)} {clock}"


def check_text(items):
    c = convert.counts(items)
    ready = c[convert.READY]
    copies = sum(c[s] for s in COPIED)          # still to make, or made already
    parts = [f"{_plural(ready, 'track')} already "
             + ("plays as it is." if ready == 1 else "play as they are.")]
    if copies:
        parts.append(f"{copies:,} {'needs' if copies == 1 else 'need'} a copy, about "
                     f"{_size(estimate_bytes(items, COPIED))}.")
    else:
        parts.append("None need a copy.")
    if c[convert.FAILED]:
        parts.append(f"{c[convert.FAILED]:,} can't be used; the list below says why.")
    return " ".join(parts)


def copies_text(items):
    c = convert.counts(items)
    made = c[convert.CONVERTED] + c[convert.REUSED]
    text = f"{_plural(made, 'copy', 'copies')} ready in the gig library."
    if c[convert.FAILED]:
        text += f" {c[convert.FAILED]:,} couldn't be used."
    return text


def set_text(st):
    return " → ".join(st.playlists) + f" · {_plural(st.track_count, 'track')}"


def _stick_line(label, chk):
    return f"{label} “{chk.name}” checked {when(chk.when)}" + (
        f": {chk.summary}" if chk.summary else "")


def steps(st):
    out = []
    chosen = bool(st.playlists)

    # 1. Your set
    if chosen:
        out.append(Step("set", "Your set", set_text(st), DONE))
    else:
        out.append(Step("set", "Your set",
                        "Tick the playlists you're playing, then put them in play order.",
                        NEXT))

    # 2. Make the tracks gig-safe
    pending = convert.counts(st.items)[convert.CONVERT] if st.items is not None else 0
    tracks_done = st.items is not None and not pending
    title = "Make the tracks gig-safe"
    if not chosen:
        out.append(Step("tracks", title, "", WAITING))
    elif st.items is None and st.earlier:
        out.append(Step("tracks", title, f"Done earlier ({when(st.earlier)}).", DONE))
        tracks_done = True
    elif st.items is None:
        out.append(Step("tracks", title, "Sortero checks every track against what the "
                        "players read.", NEXT, "Check the tracks"))
    elif pending:
        out.append(Step("tracks", title, check_text(st.items) + " Your originals are "
                        "never changed.", NEXT,
                        f"Make {_plural(pending, 'copy', 'copies')}"))
    else:
        text = check_text(st.items)
        if st.copied:
            text += " " + copies_text(st.items)
        out.append(Step("tracks", title, text, DONE))

    # 3. Main stick
    title = "Main stick" if st.direct else f"Main stick ({st.apps})"
    main_ok = bool(st.main and st.main.ready)
    if not tracks_done:
        out.append(Step("main", title, "", WAITING))
    elif main_ok:
        out.append(Step("main", title, _stick_line("Main stick", st.main) + ".", DONE))
    elif st.main:
        text = st.main.verdict + (f" {st.main.first_problem}" if st.main.first_problem
                                  else "")
        out.append(Step("main", title, text, NEXT, "Check the main stick again…"))
    elif st.direct and not st.earlier:
        text = ("Sortero writes the set to the stick for Pioneer players: the playlists in "
                "set order, beat grids and cue points. No rekordbox needed. Then it checks "
                "the stick.")
        if "Engine DJ" in st.apps:
            text += " Denon players need Engine DJ to export it (see below)."
        out.append(Step("main", title, text, NEXT, "Write the main stick…"))
    elif not st.xml_path and not st.earlier:
        out.append(Step("main", title, f"Make the playlists file, then put the set on the "
                        f"stick with {st.apps}. The steps show here.", NEXT,
                        "Make the playlists file"))
    else:
        out.append(Step("main", title, f"Put the set on the stick with {st.apps}, "
                        "following the steps below, then check it.", NEXT,
                        "Check the main stick…"))

    # 4. Ready to leave
    b = st.backup_check
    if not main_ok:
        out.append(Step("ready", "Ready to leave", "", WAITING))
    else:
        lines = ["GIG READY.", _stick_line("Main stick", st.main) + "."]
        if b and b.ready:
            lines.append(_stick_line("Backup", b) + ".")
            lines.append("Eject both sticks before you unplug them.")
        else:
            lines.append("Eject the stick before you unplug it. A backup stick is "
                         "optional: step 5.")
        out.append(Step("ready", "Ready to leave", "\n".join(lines), DONE))

    # 5. Backup stick, optional
    title = "Backup stick (optional)"
    if not tracks_done:
        out.append(Step("backup", title, "", WAITING))
    elif b and b.ready:
        out.append(Step("backup", title, _stick_line("Backup", b) + ".", DONE))
    elif b:
        out.append(Step("backup", title, b.verdict + (f" {b.first_problem}"
                                                     if b.first_problem else ""),
                        NEXT, "Write the backup again…"))
    else:
        out.append(Step("backup", title, "The set as numbered folders on a "
                        "second stick, to browse if the main stick fails.",
                        NEXT if main_ok else WAITING, "Write the backup stick…"))

    return out


def next_step(all_steps):
    """The step to do now: the first one marked next that has a button."""
    for s in all_steps:
        if s.status == NEXT and s.button:
            return s
    return None


def ready(st):
    return bool(st.main and st.main.ready)


def current(all_steps):
    """The step that should be open: the next one to do, or, once Ready to leave is
    done, that (the backup after it is optional, opened by choice)."""
    by_key = {s.key: s for s in all_steps}
    if by_key.get("ready") and by_key["ready"].status == DONE:
        return "ready"
    nxt = next((s for s in all_steps if s.status == NEXT), None)
    if nxt:
        return nxt.key
    done = [s for s in all_steps if s.status == DONE]
    return done[-1].key if done else all_steps[0].key


# --------------------------------------------------------------- cue points
CUE_ORDER = ("rekordbox", "Mixed In Key", "Mixxx")     # cues.Finder's order


def cue_sources(rekordbox_file=None, mixxx_found=True):
    """Where cue points and beat grids will come from, before anything is written."""
    where = []
    if rekordbox_file:
        where.append(f"your rekordbox collection ({rekordbox_file})")
    where.append("Mixed In Key's tags in each file")
    if mixxx_found:
        where.append("Mixxx")
    text = "Cue points and beat grids come from " + ", then ".join(where) + "."
    if not mixxx_found:
        text += " Mixxx's library isn't on this computer."
    return text


def _from(what, by_source, total):
    n = sum(by_source.values())
    if not n:
        return f"No {what} found for {'any of the ' if total > 1 else ''}" \
               f"{_plural(total, 'track') if total > 1 else 'the track'}."
    names = sorted(by_source, key=lambda s: (CUE_ORDER.index(s) if s in CUE_ORDER else 9, s))
    parts = ", ".join(f"{by_source[s]} from {s}" for s in names)
    have = ("the track" if total == 1 else f"all {_plural(total, 'track')}" if n == total
            else f"{n} of {_plural(total, 'track')}")
    return f"{what[0].upper()}{what[1:]} for {have}: {parts}."


def cue_summary(counts, total):
    """'Cue points for 7 of 9: 5 from Mixed In Key, 2 from Mixxx. Beat grids for ...'"""
    if not total:
        return ""
    return (_from("cue points", counts.get("cues", {}), total) + " "
            + _from("beat grids", counts.get("grids", {}), total))
