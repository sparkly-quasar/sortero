"""Write a track's analysis files: beat grid, cue points and waveforms, as a Pioneer player reads them.

Three files beside each other, which export.pdb and OneLibrary name by the
.DAT alone (a player finds the others by swapping the extension):

  ANLZ0000.DAT  PPTH, PVBR, PQTZ, PWAV, PWV2, PCOB (hot cues A-C), PCOB (memory)
  ANLZ0000.EXT  PPTH, PWV3, PCOB (hot cues D on), PCOB, PCO2 (hot), PCO2 (memory),
                PWV5, PWV4
  ANLZ0000.2EX  PPTH, PWV7, PWV6

The grid and cue layouts started as a port of openboxxx's writer
(libopenboxxx-export/src/anlz, GPL-2.0-or-later), whose layouts follow
pyrekordbox's (MIT). The section order, the PMAI header bytes, the PVBR
table, the shape of each cue list (hot cues first, memory cues linked to
their neighbours, the 0xFFFFFFFF ends) and the PCO2 lists follow rbxport
(crates/rbl-anlz/src/write.rs and cues.rs, GPL-2.0-or-later), which measured
them on hundreds of files rekordbox wrote: rekordbox writes PVBR into every
.DAT and rejects one without it. Everything is big-endian.

Waveforms come from waveform.py; a track that couldn't be decoded gets no
waveform sections (a player then shows none) but keeps its grid and cues.
"""
import struct

from . import waveform as W
from .devicesql import Beat, Cue

MEMORY_LIST, HOT_LIST = 0, 1
# The PMAI header's 16 bytes after its frame, as rekordbox writes them; meaning unknown.
HEADER_EXTRA = bytes((0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0))
DAT_HOT_CUES = 3                # A-C in the .DAT, the rest only in the .EXT
NONE = 0xFFFFFFFF
# The colours a player gives hot cues A-D (and I-L) by default: indexes into
# its 64-colour palette, as rbxport read them from rekordbox exports.
DEFAULT_COLOUR = {1: (43, (0xFF, 0x00, 0x17)), 2: (8, (0x00, 0xC4, 0xFF)),
                  3: (23, (0x33, 0xFF, 0x00)), 4: (60, (0x4D, 0x00, 0xFF))}


def section(fourcc, header, payload=b""):
    return fourcc + struct.pack(">II", 12 + len(header),
                                12 + len(header) + len(payload)) + header + payload


def anlz_file(sections):
    body = b"".join(sections)
    return b"PMAI" + struct.pack(">II", 28, 28 + len(body)) + HEADER_EXTRA + body


def ppth(path):
    text = path.encode("utf-16-be") + b"\0\0"
    return section(b"PPTH", struct.pack(">I", len(text)), text)


def pvbr():
    """No seek table: what rekordbox writes for a track with no VBR frames to index."""
    return section(b"PVBR", struct.pack(">I", 0), bytes(401 * 4))


def pqtz(beats):
    return section(b"PQTZ", struct.pack(">III", 0, 0x80000, len(beats)),
                   b"".join(struct.pack(">HHI", b.number, b.tempo, b.ms) for b in beats))


def _slot(cue):
    """1 for hot cue A, 0 for a memory cue."""
    return cue.hot + 1 if cue.hot >= 0 else 0


def pcpt(cue, prev=0xFFFF, nxt=0xFFFF):
    loop = cue.loop_end_ms is not None
    entry = struct.pack(">4sIIIIIHHBBHII", b"PCPT", 28, 56, _slot(cue), 0, 0x10000,
                        prev, nxt, 2 if loop else 1, 0, 1000, cue.ms,
                        cue.loop_end_ms if loop else NONE)
    return entry + bytes(56 - len(entry))


def pcob(cues, hot):
    """A cue list. Memory cues are a chain: each names the one before and after it."""
    n = len(cues)
    entries = b"".join(pcpt(c) if hot else
                       pcpt(c, 0xFFFF if i == 0 else i - 1, 0xFFFF if i == n - 1 else i + 1)
                       for i, c in enumerate(cues))
    last = NONE if hot or not n else n - 1
    return section(b"PCOB", struct.pack(">IHHI", HOT_LIST if hot else MEMORY_LIST, 0, n, last),
                   entries)


def pcp2(cue):
    loop = cue.loop_end_ms is not None
    comment = (cue.name.encode("utf-16-be") + b"\0\0") if cue.name else b""
    code, rgb = DEFAULT_COLOUR.get(_slot(cue), (0, (0, 0, 0)))
    length = 88 + len(comment)
    entry = struct.pack(">4sIIIBBHIIBB6xHHI", b"PCP2", 16, length, _slot(cue), 2 if loop else 1,
                        0, 1000, cue.ms, cue.loop_end_ms if loop else NONE, 0, 1, 0, 0,
                        len(comment))
    entry += comment + bytes((code,) + rgb)
    return entry + bytes(length - len(entry))


def pco2(cues, hot):
    return section(b"PCO2", struct.pack(">IHH", HOT_LIST if hot else MEMORY_LIST, len(cues), 0),
                   b"".join(pcp2(c) for c in cues))


def preview(fourcc, data):
    return section(fourcc, struct.pack(">II", len(data), 0x10000), data)


def scroll(fourcc, stride, data, trailer=0x00960000):
    head = struct.pack(">II", stride, len(data) // stride)
    if fourcc != b"PWV6":           # PWV6 has the shorter header
        head += struct.pack(">I", trailer)
    return section(fourcc, head, data)


def files(file_path, beats, cues, columns=None):
    """{".DAT": bytes, ".EXT": bytes, ".2EX": bytes or absent} for one track.

    `columns` are waveform.analyse's; None writes no waveforms (and no .2EX)."""
    hot = sorted((c for c in cues if c.hot >= 0), key=lambda c: c.hot)
    memory = sorted((c for c in cues if c.hot < 0), key=lambda c: c.ms)
    dat = [ppth(file_path), pvbr(), pqtz(beats)]
    ext = [ppth(file_path)]
    if columns is not None:
        dat += [preview(b"PWAV", W.pwav(columns)), preview(b"PWV2", W.pwv2(columns))]
        ext.append(scroll(b"PWV3", 1, W.pwv3(columns)))
    dat += [pcob([c for c in hot if c.hot < DAT_HOT_CUES], True), pcob(memory, False)]
    ext += [pcob([c for c in hot if c.hot >= DAT_HOT_CUES], True), pcob([], False),
            pco2(hot, True), pco2(memory, False)]
    out = {".DAT": anlz_file(dat)}
    if columns is not None:
        ext += [scroll(b"PWV5", 2, W.pwv5(columns), 0x00960305),
                scroll(b"PWV4", 6, W.pwv4(columns), 0)]
        out[".2EX"] = anlz_file([ppth(file_path), scroll(b"PWV7", 3, W.pwv7(columns)),
                                 scroll(b"PWV6", 3, W.pwv6(columns))])
    out[".EXT"] = anlz_file(ext)
    return out


def dat(file_path, beats, cues):
    """ANLZ0000.DAT alone, without waveforms."""
    return files(file_path, beats, cues)[".DAT"]


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
