"""Put a gig on a stick as a rekordbox Device Library, without rekordbox.

What rekordbox's own export writes, and what every Pioneer player since the
CDJ-900 reads in rekordbox mode:

  PIONEER/rekordbox/export.pdb                    the database (devicesql.py)
  PIONEER/USBANLZ/P001/00000001/ANLZ0000.DAT      each track's grid and cues (anlz.py)
  Contents/Artist/Album/file.mp3                  the audio

The input is a rekordbox XML, the same one Gig prep writes for rekordbox to
import (rbxml.py): its tracks, tempo stretches, cue points and playlists. So
the stick and the XML can't disagree, and any rekordbox collection XML works.

Safety: a stick that already carries a library Sortero didn't write (a
rekordbox export, or the OneLibrary database the newest players read) is
refused rather than overwritten. Sortero marks its own exports (MARKER) and
keeps a list of the audio it copied, so a second run replaces only its own
files and anything else on the stick is left alone.

Not written yet: the OneLibrary database (exportLibrary.db) the CDJ-3000X,
XDJ-AZ, OPUS-QUAD and OMNIS-DUO read instead of export.pdb, waveforms, and
artwork. Nothing here has been played on a real player yet.
"""
import json, os, shutil, time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import anlz, devicesql
from .backup import TMP, _copy, fat_name
from .cues import Grid, Mark, location_path, _float
from .pdb import EXTRA, RELATIVE
from ..version import __version__

MARKER = os.path.join("PIONEER", "rekordbox", ".sortero-export.json")
CONTENTS = "Contents"


class StickError(Exception):
    pass


@dataclass
class Plan:
    tracks: list                    # devicesql.Track
    playlists: list                 # devicesql.Playlist
    sources: dict                   # track id -> the audio file on this computer
    missing: list = field(default_factory=list)     # (title, path) not found


@dataclass
class Result:
    tracks: int = 0
    playlists: int = 0
    copied: list = field(default_factory=list)      # stick-relative paths
    unchanged: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    missing: list = field(default_factory=list)


def _int(v):
    x = _float(v)
    return int(x) if x is not None else 0


def anlz_path(track_id):
    return f"/PIONEER/USBANLZ/P{track_id % 1000:03d}/{track_id:08X}/ANLZ0000.DAT"


# -------------------------------------------------------------------- plan
def plan(xml_path, exists=os.path.isfile):
    """Tracks, playlists and where each track's audio is, from a rekordbox XML."""
    try:
        root = ET.parse(xml_path).getroot()
    except (OSError, ET.ParseError) as e:
        raise StickError(f"couldn't read the playlists file ({e})")
    tracks, sources, missing, by_key, taken = [], {}, [], {}, set()
    for el in root.iterfind("COLLECTION/TRACK"):
        tid, loc = _int(el.get("TrackID")), el.get("Location")
        if not tid or not loc:
            continue
        src = location_path(loc)
        title = el.get("Name") or os.path.splitext(os.path.basename(src))[0]
        if not exists(src):
            missing.append((title, src))
            continue
        artist, album = el.get("Artist") or "", el.get("Album") or ""
        folder = f"/{CONTENTS}/{fat_name(artist or 'Unknown Artist')}/" \
                 f"{fat_name(album or 'Unknown Album')}/"
        stem, ext = os.path.splitext(fat_name(os.path.basename(src)))
        name, n = stem + ext, 2
        while (folder + name).lower() in taken:      # FAT32 ignores case
            name, n = f"{stem} ({n}){ext}", n + 1
        taken.add((folder + name).lower())
        duration = _int(el.get("TotalTime"))
        grids = [Grid(_float(t.get("Inizio"), 0.0), _float(t.get("Bpm"), 0.0),
                      t.get("Metro") or "4/4", _int(t.get("Battito")) or 1)
                 for t in el.findall("TEMPO")]
        marks = [Mark(_float(m.get("Start"), 0.0), m.get("Name") or "",
                      _int(m.get("Num")) if m.get("Num") is not None else -1,
                      _int(m.get("Type")), _float(m.get("End")))
                 for m in el.findall("POSITION_MARK") if m.get("Start") is not None]
        t = devicesql.Track(
            tid, title, folder + name, anlz_path(tid), artist=artist, album=album,
            genre=el.get("Genre") or "", key=el.get("Tonality") or "",
            comment=el.get("Comments") or "", year=_int(el.get("Year")),
            track_number=_int(el.get("TrackNumber")), duration=duration,
            bitrate=_int(el.get("BitRate")), sample_rate=_int(el.get("SampleRate")),
            file_size=_int(el.get("Size")) or os.path.getsize(src),
            bpm=_float(el.get("AverageBpm"), 0.0), rating=0,
            beats=anlz.beats_from(grids, duration), cues=anlz.cues_from(marks))
        tracks.append(t)
        sources[tid] = src
        by_key[tid] = t
    playlists = []

    def walk(node, parent):
        for child in node.findall("NODE"):
            if child.get("Type") == "0":
                p = devicesql.Playlist(len(playlists) + 1, child.get("Name") or "",
                                       parent, folder=True)
                playlists.append(p)
                walk(child, p.id)
            elif child.get("Type") == "1":
                ids = [_int(t.get("Key")) for t in child.findall("TRACK")]
                playlists.append(devicesql.Playlist(
                    len(playlists) + 1, child.get("Name") or "", parent,
                    tracks=[i for i in ids if i in by_key]))

    top = root.find("PLAYLISTS/NODE")               # ROOT, never shown
    if top is not None:
        walk(top, 0)
    return Plan(tracks, playlists, sources, missing)


# ------------------------------------------------------------------- stick
def _marker(stick):
    try:
        with open(os.path.join(stick, MARKER), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def foreign(stick):
    """Why this stick's library isn't Sortero's to replace, or None if it is (or there's none)."""
    mark = _marker(stick)
    pdb_path = os.path.join(stick, RELATIVE)
    # rekordbox exporting over Sortero's library leaves the marker behind; the
    # database it wrote won't be the size Sortero's was.
    ours = bool(mark) and (not os.path.isfile(pdb_path)
                           or os.path.getsize(pdb_path) == mark.get("pdb_size"))
    for rel in [RELATIVE] + [os.path.join(os.path.dirname(RELATIVE), x) for x in EXTRA]:
        if os.path.exists(os.path.join(stick, rel)):
            if not ours:
                return (f"it already has a rekordbox library ({rel.replace(os.sep, '/')}) "
                        "that Sortero didn't write")
            if os.path.basename(rel) in EXTRA:
                return (f"rekordbox has added {os.path.basename(rel)} since Sortero wrote "
                        "it, so it's rekordbox's library now")
    return None


def _manifest(stick):
    return (_marker(stick) or {}).get("audio", [])


def _put(stick, rel, data):
    """Write a file whole, flushed, then named: a pulled stick leaves the old one or none."""
    path = os.path.join(stick, *rel.strip("/").split("/"))
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, TMP + os.path.basename(path))
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def needed_space(p, stick):
    total = 0
    for t in p.tracks:
        dest = os.path.join(stick, *t.file_path.strip("/").split("/"))
        if not (os.path.isfile(dest) and os.path.getsize(dest) == os.path.getsize(
                p.sources[t.id])):
            total += os.path.getsize(p.sources[t.id])
    return total


def write(stick, xml_path, progress=None, log=print):
    """Write the playlists file's gig to `stick` as a Device Library. Returns a Result."""
    if not os.path.isdir(stick):
        raise StickError(f"{stick} isn't there")
    why = foreign(stick)
    if why:
        raise StickError(f"Not writing to this stick: {why}. Use another stick, or "
                         "format this one first.")
    p = plan(xml_path)
    if not p.tracks:
        raise StickError("The playlists file has no tracks Sortero can find on this computer.")
    need = needed_space(p, stick)
    free = shutil.disk_usage(stick).free
    if need > free:
        raise StickError(f"The stick needs {need / 1e9:.1f} GB free and has "
                         f"{free / 1e9:.1f} GB.")
    res = Result(len(p.tracks), sum(1 for x in p.playlists if not x.folder),
                 missing=p.missing)
    wanted = {t.file_path.strip("/") for t in p.tracks}
    for rel in _manifest(stick):                    # our old copies not in this gig
        path = os.path.join(stick, *rel.split("/"))
        if rel not in wanted and os.path.isfile(path):
            os.remove(path)
            res.removed.append(rel)
    shutil.rmtree(os.path.join(stick, "PIONEER", "USBANLZ"), ignore_errors=True)
    total = len(p.tracks)
    for i, t in enumerate(p.tracks, 1):
        rel, src = t.file_path.strip("/"), p.sources[t.id]
        dest = os.path.join(stick, *rel.split("/"))
        if os.path.isfile(dest) and os.path.getsize(dest) == os.path.getsize(src):
            res.unchanged.append(rel)
        else:
            _copy(src, dest)
            res.copied.append(rel)
        _put(stick, t.analyze_path, anlz.dat(t.file_path, t.beats, t.cues))
        if progress:
            progress(i, total)
    # The database last: until it's there, no player sees a half-written library.
    db = devicesql.build(p.tracks, p.playlists)
    _put(stick, MARKER.replace(os.sep, "/"), json.dumps(
        {"written_by": f"Sortero {__version__}", "when": time.strftime("%Y-%m-%d %H:%M"),
         "pdb_size": len(db), "audio": sorted(wanted)}, indent=1).encode("utf-8"))
    _put(stick, RELATIVE.replace(os.sep, "/"), db)
    log(f"Wrote {res.tracks} tracks and {res.playlists} playlists to {stick}: "
        f"{len(res.copied)} copied, {len(res.unchanged)} already there.")
    return res
