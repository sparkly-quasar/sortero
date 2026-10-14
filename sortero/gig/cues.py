"""Cue points and beat grids for the tracks in a gig, from wherever they already are.

Three places can hold them, and a track takes them from the first that has
any, rather than a mix of all three:

  1. a rekordbox XML of your collection (rekordbox: File > Export Collection
     in xml format, or the rekordbox.xml Mixed In Key keeps up to date)
  2. Mixxx's library
  3. Mixed In Key's own analysis, in the file's tags

Times are seconds from the start of the audio. Sources:

  rekordbox XML  POSITION_MARK Start/End and TEMPO Inizio are seconds; Type 0
                 is a cue, 4 a loop; Num -1 is a memory cue, 0-7 hot cues A-H.
                 rekordbox's xml_format_list.pdf names the fields; pyrekordbox
                 (rbxml.py) documents their meaning.
  Mixxx          cues.position is an "engine sample position", frames x 2
                 (FramePos::fromEngineSamplePos in src/audio/frame.h), so
                 seconds = position / 2 / samplerate. Types from
                 src/track/cueinfo.h: 1 hot cue, 2 main cue, 4 loop.
  Mixed In Key   GEOB CuePoints: base64 JSON, cue "time" in milliseconds.
                 GEOB BeatGrid: "beats" in seconds, "tempo" in BPM. Units
                 confirmed on a real MIK file: every cue fell on a bar line.
"""
import base64, binascii, json, os, sqlite3, urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import tagcopy

CUE, LOOP = 0, 4                # rekordbox POSITION_MARK types
MEMORY = -1                     # Num for a memory cue; 0-7 are hot cues A-H
HOT_CUES = 8


@dataclass
class Mark:
    start: float
    name: str = ""
    num: int = MEMORY
    type: int = CUE
    end: float = None


@dataclass
class Grid:
    start: float                # the first beat, seconds
    bpm: float
    meter: str = "4/4"
    beat: int = 1               # which beat of the bar the first one is


@dataclass
class Marks:
    source: str
    marks: list = field(default_factory=list)
    grids: list = field(default_factory=list)

    def shifted(self, seconds):
        """The same marks, moved later by `seconds` (see rbxml.MP3_COPY_DELAY)."""
        if not seconds:
            return self
        return Marks(self.source,
                     [Mark(m.start + seconds, m.name, m.num, m.type,
                           None if m.end is None else m.end + seconds) for m in self.marks],
                     [Grid(g.start + seconds, g.bpm, g.meter, g.beat) for g in self.grids])


def key(path):
    """How paths are compared across sources: absolute, normalised, case per OS."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


# --------------------------------------------------------- Mixed In Key
def _mik(frames, desc):
    for fr in frames:
        if getattr(fr, "FrameID", "") == "GEOB" and fr.desc == desc:
            try:
                return json.loads(base64.b64decode(b"".join(fr.data.split())))
            except (binascii.Error, ValueError):
                return None
    return None


def from_mik(path):
    """Marks from Mixed In Key's analysis in the file's tags, or None."""
    try:
        frames, _ = tagcopy.read(path)
    except Exception:
        return None
    cues, grid = _mik(frames, "CuePoints"), _mik(frames, "BeatGrid")
    out = Marks("Mixed In Key")
    if isinstance(cues, dict):
        times = sorted((c.get("time"), c.get("name") or "") for c in cues.get("cues", [])
                       if isinstance(c.get("time"), (int, float)))
        # Every cue as a memory cue, and the first eight as hot cues A-H as well:
        # cue points MIK finds are where a mix can start, and on the decks
        # either kind is where you'd look for them.
        for i, (ms, name) in enumerate(times):
            out.marks.append(Mark(ms / 1000, name))
            if i < HOT_CUES:
                out.marks.append(Mark(ms / 1000, name, num=i))
    if isinstance(grid, dict) and grid.get("beats") and grid.get("tempo"):
        out.grids.append(Grid(float(grid["beats"][0]), float(grid["tempo"])))
    return out if out.marks or out.grids else None


# -------------------------------------------------------------- rekordbox
def location_path(location):
    """A rekordbox Location URI as a local path."""
    p = urllib.parse.unquote(location)
    for prefix in ("file://localhost", "file://"):
        if p.startswith(prefix):
            p = p[len(prefix):]
            break
    if len(p) > 2 and p[0] == "/" and p[2] == ":":      # /C:/Music/... on Windows
        p = p[1:]
    return os.path.normpath(p)


def _float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def from_rekordbox_xml(xml_path):
    """{path key: Marks} for every track with cues or a grid in a rekordbox XML."""
    out = {}
    for _, el in ET.iterparse(xml_path, events=("end",)):
        if el.tag != "TRACK" or not el.get("Location"):
            continue
        m = Marks("rekordbox")
        for pm in el.findall("POSITION_MARK"):
            start = _float(pm.get("Start"))
            if start is None:
                continue
            m.marks.append(Mark(start, pm.get("Name") or "", int(_float(pm.get("Num"), -1)),
                                int(_float(pm.get("Type"), 0)), _float(pm.get("End"))))
        for t in el.findall("TEMPO"):
            start, bpm = _float(t.get("Inizio")), _float(t.get("Bpm"))
            if start is not None and bpm:
                m.grids.append(Grid(start, bpm, t.get("Metro") or "4/4",
                                    int(_float(t.get("Battito"), 1))))
        if m.marks or m.grids:
            out[key(location_path(el.get("Location")))] = m
        el.clear()
    return out


# ------------------------------------------------------------------ Mixxx
MIXXX_HOT, MIXXX_MAIN, MIXXX_LOOP = 1, 2, 4


def from_mixxx(db):
    """{path key: Marks} from Mixxx's library. Read-only; Mixxx can stay open."""
    from .. import mixxx
    out = {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT tl.location, l.samplerate, c.type, c.position, c.length, c.hotcue, "
            "c.label FROM cues c JOIN library l ON l.id = c.track_id "
            "JOIN track_locations tl ON tl.id = l.location").fetchall()
        grids = con.execute(
            "SELECT tl.location, l.samplerate, l.bpm, l.beats, l.beats_version "
            "FROM library l JOIN track_locations tl ON tl.id = l.location "
            "WHERE l.beats IS NOT NULL").fetchall()
    finally:
        con.close()
    for loc, rate, kind, pos, length, hot, label in rows:
        if not rate or pos is None or pos < 0:
            continue
        start = pos / 2 / rate
        m = out.setdefault(key(loc), Marks("Mixxx"))
        hot = hot if hot is not None and 0 <= hot < HOT_CUES else MEMORY
        if kind == MIXXX_HOT:
            m.marks.append(Mark(start, label or "", hot))
        elif kind == MIXXX_MAIN:
            m.marks.append(Mark(start, label or ""))
        elif kind == MIXXX_LOOP and length and length > 0:
            m.marks.append(Mark(start, label or "", hot, LOOP, start + length / 2 / rate))
    for loc, rate, bpm, beats, version in grids:
        if not rate or not bpm or version != mixxx.GRID:
            continue
        try:
            first = mixxx.grid_first_beat(bytes(beats))
        except Exception:
            continue
        out.setdefault(key(loc), Marks("Mixxx")).grids.append(Grid(first / rate, float(bpm)))
    return {k: m for k, m in out.items() if m.marks or m.grids}


# ---------------------------------------------------------------- choose
class Finder:
    """Looks up a track's marks in each source, in order, the first that has any."""

    def __init__(self, rekordbox_xml=None, mixxx_db=None, log=print):
        self.rekordbox, self.mixxx = {}, {}
        if rekordbox_xml:
            try:
                self.rekordbox = from_rekordbox_xml(rekordbox_xml)
            except (OSError, ET.ParseError) as e:
                log(f"  ! couldn't read {rekordbox_xml}: {e}")
        if mixxx_db:
            try:
                self.mixxx = from_mixxx(mixxx_db)
            except sqlite3.Error as e:
                log(f"  ! couldn't read Mixxx's library: {e}")

    def find(self, original):
        k = key(original)
        return self.rekordbox.get(k) or self.mixxx.get(k) or from_mik(original)
