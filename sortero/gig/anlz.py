"""Write a track's ANLZ0000.DAT: its beat grid and cue points, as a Pioneer player reads them.

Ported from openboxxx's writer (libopenboxxx-export/src/anlz, GPL-2.0-or-later),
whose layouts follow pyrekordbox's (MIT) and were checked field by field
against files rekordbox wrote. Everything is big-endian.

A PMAI header, then tagged sections, each "fourcc, header length, whole length":

  PPTH  the audio file's path on the stick, UTF-16BE, as export.pdb names it
  PQTZ  the beat grid: every beat's place in the bar, tempo and time
  PCOB  memory cues, then PCOB again for hot cues A-H, one PCPT per cue

Waveforms (PWAV and friends) and the .EXT file's extended cues aren't written
yet: a player shows no waveform, but the grid, cues and loops work.
"""
import struct

from .devicesql import Beat, Cue

MEMORY_LIST, HOT_LIST = 0, 1


def _tag(fourcc, header_len, body):
    """One tagged section; `body` is everything after the 12-byte tag header."""
    return fourcc + struct.pack(">II", header_len, 12 + len(body)) + body


def ppth(path):
    text = path.encode("utf-16-be") + b"\0\0"
    return _tag(b"PPTH", 16, struct.pack(">I", len(text)) + text)


def pqtz(beats):
    body = struct.pack(">III", 0, 0x80000, len(beats))
    body += b"".join(struct.pack(">HHI", b.number, b.tempo, b.ms) for b in beats)
    return _tag(b"PQTZ", 24, body)


def pcpt(cue):
    loop = cue.loop_end_ms is not None
    body = struct.pack(">IIIHHBBHII", cue.hot + 1 if cue.hot >= 0 else 0, 4, 0x10000,
                       0xFFFF, 0xFFFF, 2 if loop else 1, 0, 1000, cue.ms,
                       cue.loop_end_ms if loop else 0xFFFFFFFF) + bytes(16)
    return _tag(b"PCPT", 0x1C, body)


def pcob(cues, hot):
    mine = sorted((c for c in cues if (c.hot >= 0) == hot),
                  key=lambda c: (c.hot if hot else 0, c.ms))
    body = struct.pack(">IHHI", HOT_LIST if hot else MEMORY_LIST, 0, len(mine), len(mine))
    return _tag(b"PCOB", 24, body + b"".join(pcpt(c) for c in mine))


def dat(file_path, beats, cues):
    """ANLZ0000.DAT's bytes."""
    body = ppth(file_path) + pqtz(beats) + pcob(cues, False) + pcob(cues, True)
    return b"PMAI" + struct.pack(">II", 28, 28 + len(body)) + bytes(16) + body


# ------------------------------------------------- from Sortero's marks
def beats_from(grids, duration):
    """Every beat to the end of the track, from tempo stretches (cues.Grid: first
    beat in seconds, BPM, which beat of the bar it is)."""
    out = []
    grids = sorted((g for g in grids if g.bpm > 0), key=lambda g: g.start)
    for i, g in enumerate(grids):
        end = grids[i + 1].start if i + 1 < len(grids) else duration
        period, number, t = 60.0 / g.bpm, (int(g.beat) - 1) % 4 + 1, g.start
        tempo = int(round(g.bpm * 100))
        while t < end - 1e-6:
            if t >= 0:
                out.append(Beat(number, tempo, int(round(t * 1000))))
            number = number % 4 + 1
            t += period
    return out


def cues_from(marks):
    """Hot cues, memory cues and loops from cues.Mark (seconds; num -1 memory, 0-7 hot)."""
    from .cues import LOOP, HOT_CUES
    out = []
    for m in marks:
        if m.start < 0 and m.start > -0.5:
            start = 0               # a hot cue a hair before zero is still that hot cue
        elif m.start < 0:
            continue
        else:
            start = m.start
        hot = m.num if 0 <= m.num < HOT_CUES else -1
        loop_end = (int(round(m.end * 1000)) if m.type == LOOP and m.end is not None
                    and m.end > start else None)
        out.append(Cue(int(round(start * 1000)), hot, loop_end, m.name))
    seen, unique = set(), []
    for c in out:                   # one memory cue per millisecond
        k = (c.hot, c.ms) if c.hot < 0 else (c.hot,)
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return unique
