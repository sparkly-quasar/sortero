"""Put a gig on a stick as a rekordbox Device Library, without rekordbox.

What rekordbox's own export writes, and what every Pioneer player since the
CDJ-900 reads in rekordbox mode:

  PIONEER/rekordbox/export.pdb                    the database (devicesql.py)
  PIONEER/USBANLZ/P001/00000001/ANLZ0000.DAT      each track's grid, cues and waveforms
                     .../ANLZ0000.EXT, .2EX         (anlz.py, waveform.py)
  PIONEER/Artwork/00001/a1.jpg, a1_m.jpg ...       artwork, 80 and 240 px (artwork.py)
  Contents/Artist/Album/file.mp3                  the audio

The input is a rekordbox XML, the same one Gig prep writes for rekordbox to
import (rbxml.py): its tracks, tempo stretches, cue points and playlists. So
the stick and the XML can't disagree, and any rekordbox collection XML works.

Safety: a stick that already carries a library Sortero didn't write (a
rekordbox export, or the OneLibrary database the newest players read) is
refused rather than overwritten. Sortero marks its own exports (MARKER) and
keeps a list of the audio it copied, so a second run replaces only its own
files and anything else on the stick is left alone.

  PIONEER/rekordbox/exportLibrary.db              the same, as OneLibrary (onelibrary.py)

OneLibrary is what the CDJ-3000X, XDJ-AZ, OPUS-QUAD and OMNIS-DUO read instead
of export.pdb; it's written whenever Sortero has SQLCipher, which the app
always carries.

  Engine Library/Database2/m.db                   the same for Denon players (enginelib.py)

The Engine DJ library Denon's players read, written when the chosen players
include them (write(engine=True)). It names the same audio under Contents/,
so one stick carries both makers' libraries over one copy of the tracks. An
Engine library Sortero didn't write (from Engine DJ, or one Engine has since
upgraded or added to) is refused like a rekordbox one.

Nothing here has been played on a real player yet.
"""
import concurrent.futures, json, os, shutil, time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import anlz, artwork, devicesql, enginelib, onelibrary, probe, waveform
from .backup import TMP, _copy, fat_name
from .cues import Grid, Mark, location_path, _float
from .pdb import EXTRA, RELATIVE
from ..version import __version__

MARKER = os.path.join("PIONEER", "rekordbox", ".sortero-export.json")
CONTENTS = "Contents"
DEFAULT_RATE = 44100            # for Engine's sample positions, when nothing says otherwise


class StickError(Exception):
    pass


@dataclass
class Plan:
    tracks: list                    # devicesql.Track
    playlists: list                 # devicesql.Playlist
    sources: dict                   # track id -> the audio file on this computer
    missing: list = field(default_factory=list)     # (title, path) not found
    grids: dict = field(default_factory=dict)       # track id -> [cues.Grid], for Engine


@dataclass
class Result:
    tracks: int = 0
    playlists: int = 0
    copied: list = field(default_factory=list)      # stick-relative paths
    unchanged: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    onelibrary: bool = False
    no_waveform: list = field(default_factory=list)  # (title, why)
    artwork: int = 0                                # distinct images written
    engine: bool = False                            # wrote the Engine DJ library
    engine_repeats: list = field(default_factory=list)  # (playlist, title) Engine can't repeat
    engine_dropped_cues: list = field(default_factory=list)  # (title, n) past 8 pads / loops


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
    tracks, sources, missing, by_key, taken, grids_of = [], {}, [], {}, set(), {}
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
        grids_of[tid] = grids
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
    return Plan(tracks, playlists, sources, missing, grids_of)


# ------------------------------------------------------------------- stick
def _marker(stick):
    try:
        with open(os.path.join(stick, MARKER), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _engine_identity(built):
    """What the marker remembers of the Engine library Sortero wrote: enough to
    know it again, and to see Engine DJ has added to or upgraded it since."""
    return {"uuid": built.uuid, "tracks": built.tracks, "playlists": built.playlists,
            "schema": list(enginelib.SCHEMA_VERSION)}


def engine_foreign(stick, mark=None):
    """Why this stick's Engine library isn't Sortero's to replace, or None if it is
    (or there's none). Players keep history beside m.db (hm.db and others); that's
    theirs and is left alone, but m.db itself must be the one Sortero wrote."""
    mark = _marker(stick) if mark is None else mark
    path = os.path.join(stick, enginelib.DATABASE)
    theirs = ("it already has an Engine DJ library ({}) that Sortero didn't write")
    if os.path.isfile(os.path.join(stick, enginelib.OLD_DATABASE)):
        return theirs.format("Engine Library/m.db")
    if not os.path.exists(path):
        return None
    mine = (mark or {}).get("engine")
    if not mine:
        return theirs.format("Engine Library/Database2/m.db")
    try:
        now = enginelib.info(path)
    except enginelib.EngineError:
        return "its Engine DJ library can't be read, so Sortero won't replace it"
    if now["uuid"] != mine.get("uuid"):
        return theirs.format("Engine Library/Database2/m.db")
    if list(now["schema"]) != list(mine.get("schema") or []):
        return ("Engine has upgraded the Engine DJ library Sortero wrote, so it's Engine "
                "DJ's library now")
    if (now["tracks"], now["playlists"]) != (mine.get("tracks"), mine.get("playlists")):
        return ("Engine DJ has added to the Engine library since Sortero wrote it, so it's "
                "Engine DJ's library now")
    return None


def wrote_engine(stick):
    """Is the stick's Engine library the one Sortero wrote?"""
    mark = _marker(stick)
    return bool(mark and mark.get("engine")) and engine_foreign(stick, mark) is None


def foreign(stick, engine=True):
    """Why this stick's library isn't Sortero's to replace, or None if it is (or there's none).

    engine=True asks it of the Engine DJ library too, which Sortero only
    replaces when it's writing one."""
    mark = _marker(stick)
    if engine:
        why = engine_foreign(stick, mark)
        if why:
            return why
    pdb_path = os.path.join(stick, RELATIVE)
    ol_path = os.path.join(stick, os.path.dirname(RELATIVE), onelibrary.FILE_NAME)
    # rekordbox exporting over Sortero's library leaves the marker behind; the
    # databases it wrote won't be the sizes Sortero's were.
    ours = bool(mark) and (not os.path.isfile(pdb_path)
                           or os.path.getsize(pdb_path) == mark.get("pdb_size"))
    for rel in [RELATIVE] + [os.path.join(os.path.dirname(RELATIVE), x) for x in EXTRA]:
        path = os.path.join(stick, rel)
        if not os.path.exists(path):
            continue
        if not ours:
            return (f"it already has a rekordbox library ({rel.replace(os.sep, '/')}) "
                    "that Sortero didn't write")
        mine = path == ol_path and os.path.getsize(path) == mark.get("onelibrary_size")
        if os.path.basename(rel) in EXTRA and not mine:
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


def _engine_audio(t, src, grids, waves):
    """enginelib.Audio for a track: its sample rate (from the XML, or ffprobe), its
    length in samples (from the waveform's 1/150 s columns, or the XML's seconds),
    grid, and overview waveform."""
    rate = t.sample_rate
    if not rate:
        try:
            rate = probe.probe(src).rate
        except Exception:
            rate = 0
    rate = rate or DEFAULT_RATE
    pwv7 = (waves or {}).get("PWV7") or b""
    samples = (len(pwv7) // 3 * rate // waveform.PER_SECOND if pwv7
               else t.duration * rate)
    return enginelib.Audio(rate, samples, grids,
                           enginelib.overview(rate, samples, pwv7) if pwv7 else b"")


def _waveform(src, cache):
    try:
        return waveform.analyse(src, cache), None
    except waveform.WaveformError as e:
        return None, str(e)


def write(stick, xml_path, progress=None, log=print, workers=None, cache=None, engine=True):
    """Write the playlists file's gig to `stick` as a Device Library. Returns a Result.

    engine=True (Denon players among tonight's) writes the Engine DJ library too.

    Waveforms come from the copy on this computer, not the stick (no reading every
    track back over USB), a few tracks ahead of the copying, and are remembered
    (waveform.Cache), so writing the stick again only analyses what changed."""
    if not os.path.isdir(stick):
        raise StickError(f"{stick} isn't there")
    mark = _marker(stick) or {}
    why = foreign(stick, engine)
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
    for old in ("USBANLZ", "Artwork"):              # Sortero's own, from the last run
        shutil.rmtree(os.path.join(stick, "PIONEER", old), ignore_errors=True)
    art = artwork.Collector()
    total = len(p.tracks)
    cache = cache or waveform.Cache()
    workers = workers or max(1, min(4, os.cpu_count() or 1))
    ahead = 2 * workers             # bounded: a waveform is megabytes until it's written
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    waves, queued, audio = {}, 0, {}

    def queue(upto):
        nonlocal queued
        for t in p.tracks[queued:upto]:
            waves[t.id] = pool.submit(_waveform, p.sources[t.id], cache)
        queued = max(queued, min(upto, total))
    try:
        for i, t in enumerate(p.tracks, 1):
            queue(i + ahead)
            rel, src = t.file_path.strip("/"), p.sources[t.id]
            dest = os.path.join(stick, *rel.split("/"))
            if os.path.isfile(dest) and os.path.getsize(dest) == os.path.getsize(src):
                res.unchanged.append(rel)
            else:
                _copy(src, dest)
                res.copied.append(rel)
            t.artwork_id = art.add(src)
            columns, why = waves.pop(t.id).result()
            if why:
                res.no_waveform.append((t.title, why))
            base = t.analyze_path[:-len(".DAT")]
            for ext, data in anlz.files(t.file_path, t.beats, t.cues, columns).items():
                _put(stick, base + ext, data)
            if engine:
                audio[t.id] = _engine_audio(t, src, p.grids.get(t.id, []), columns)
            if progress:
                progress(i, total)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    cache.prune()
    # The databases last: until they're there, no player sees a half-written library.
    for rel, data in art.files.items():
        _put(stick, rel, data)
    res.artwork = len(art.ids)
    db = devicesql.build(p.tracks, p.playlists, artwork=art.table())
    folder = os.path.join(stick, os.path.dirname(RELATIVE))
    os.makedirs(folder, exist_ok=True)
    ol_path = os.path.join(folder, onelibrary.FILE_NAME)
    ol_size = None
    if onelibrary.available():
        name = os.path.basename(os.path.normpath(stick))
        onelibrary.build(ol_path, p.tracks, p.playlists, device_name=name,
                         artwork=art.table())
        ol_size = os.path.getsize(ol_path)
        res.onelibrary = True
    else:
        log(f"  ! No OneLibrary: {onelibrary.MISSING}.")
        if os.path.exists(ol_path):
            os.remove(ol_path)      # Sortero's from an earlier run; it no longer matches
    engine_path = os.path.join(stick, enginelib.DATABASE)
    engine_mark = None
    if engine:
        big = {i: art.files[artwork.path(i, medium=True)] for i in art.ids.values()}
        try:
            # The same identity as last time, so a player's history stays with it
            built = enginelib.build(engine_path, p.tracks, p.playlists, audio, art=big,
                                    library_uuid=(mark.get("engine") or {}).get("uuid"))
        except enginelib.EngineError as e:
            raise StickError(f"Sortero {e}.")
        engine_mark = _engine_identity(built)
        res.engine = True
        res.engine_repeats, res.engine_dropped_cues = built.repeats, built.dropped_cues
    elif mark.get("engine") and os.path.isfile(engine_path) and not engine_foreign(stick, mark):
        os.remove(engine_path)      # Sortero's, from when Denon players were chosen
    _put(stick, MARKER.replace(os.sep, "/"), json.dumps(
        {"written_by": f"Sortero {__version__}", "when": time.strftime("%Y-%m-%d %H:%M"),
         "pdb_size": len(db), "onelibrary_size": ol_size, "engine": engine_mark,
         "audio": sorted(wanted)},
        indent=1).encode("utf-8"))
    _put(stick, RELATIVE.replace(os.sep, "/"), db)
    log(f"Wrote {res.tracks} tracks and {res.playlists} playlists to {stick}"
        + (", with an Engine DJ library for Denon players" if res.engine else "")
        + f": {len(res.copied)} copied, {len(res.unchanged)} already there.")
    for name, title in res.engine_repeats:
        log(f"  ! Engine DJ lists a track once per playlist: {title} is in {name} once.")
    for title, n in res.engine_dropped_cues:
        log(f"  ! Engine DJ has 8 pads and 8 loops: {n} of {title}'s cues didn't fit.")
    return res
