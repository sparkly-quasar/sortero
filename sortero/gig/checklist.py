"""The Gig prep checklist: which steps are done, which comes next, and what to say.

Kept apart from the screen so it can be tested without a window. The screen
hands over what it knows (State) and draws whatever steps() returns.
"""
from dataclasses import dataclass, field

from . import convert

DONE, NEXT, WAITING, SOON = "done", "next", "waiting", "soon"
MARKS = {DONE: "✓", NEXT: "→", WAITING: "○", SOON: "○"}

# Bytes per second of audio, for the space a copy will take.
RATE_BYTES = {convert.MP3: 320_000 // 8, convert.AIFF: 44_100 * 2 * 2}


@dataclass
class State:
    playlists: list = field(default_factory=list)   # chosen, in set order
    track_count: int = 0
    items: list = None          # convert.plan's result, once the tracks are checked
    copied: bool = False        # convert.run has finished on these items
    report: object = None       # the last verify.Report, if a stick was checked


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
        parts.append(f"{c[convert.FAILED]:,} can't be used; the report says why.")
    return " ".join(parts)


def copies_text(items):
    c = convert.counts(items)
    made = c[convert.CONVERTED] + c[convert.REUSED]
    text = f"{_plural(made, 'copy', 'copies')} ready in the gig library."
    if c[convert.FAILED]:
        text += f" {c[convert.FAILED]:,} couldn't be used; the report says why."
    return text


def steps(st):
    out = []
    chosen = bool(st.playlists)
    if chosen:
        out.append(Step("playlists", "Choose the playlists",
                        f"{_plural(len(st.playlists), 'playlist')}, "
                        f"{_plural(st.track_count, 'track')}, in this order: "
                        + ", ".join(st.playlists) + ".", DONE))
    else:
        out.append(Step("playlists", "Choose the playlists",
                        "Tick the playlists you're playing, in the order you'll play "
                        "them.", NEXT))

    if not chosen:
        out.append(Step("check", "Check the tracks", "", WAITING))
    elif st.items is None:
        out.append(Step("check", "Check the tracks",
                        "See which tracks play on the players as they are, and which "
                        "need a gig-safe copy.", NEXT, "Check tracks"))
    else:
        out.append(Step("check", "Check the tracks", check_text(st.items), DONE,
                        "Check again"))

    pending = (convert.counts(st.items)[convert.CONVERT] if st.items is not None else 0)
    if st.items is None:
        out.append(Step("copies", "Make gig-safe copies", "", WAITING))
    elif pending:
        out.append(Step("copies", "Make gig-safe copies",
                        f"{_plural(pending, 'track')} to copy, about "
                        f"{_size(estimate_bytes(st.items))}. Your originals are never "
                        "changed.", NEXT, f"Make {_plural(pending, 'copy', 'copies')}"))
    else:
        out.append(Step("copies", "Make gig-safe copies",
                        copies_text(st.items) if st.copied else "Nothing needs a copy.",
                        DONE))

    out.append(Step("stick", "Put them on a stick (coming next)", "", SOON))

    copies_done = st.items is not None and not pending
    if st.report is not None:
        out.append(Step("verify", "Check the stick", st.report.verdict,
                        DONE if st.report.ready else NEXT, "Check a stick…"))
    else:
        out.append(Step("verify", "Check the stick",
                        "Plug it in and check it plays everywhere: file system, "
                        "formats, and every track decoded from start to finish."
                        if copies_done else "", NEXT if copies_done else WAITING,
                        "Check a stick…"))
    return out


def next_step(all_steps):
    """The step the main button should do: the first one marked next that has a button."""
    for s in all_steps:
        if s.status == NEXT and s.button:
            return s
    return None
