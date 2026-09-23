"""Mixxx's library: read and correct a track's BPM and beatgrid.

Mixxx keeps its own BPM and beatgrid per track in mixxxdb.sqlite and never
goes back to the file's BPM tag once it has analysed a track, so fixing the
tag alone changes nothing in Mixxx. Worse, with "sync metadata to files" on,
Mixxx writes its own (wrong) number back over a corrected tag.

Only ever touched while Mixxx is closed: it caches the library in memory and
would overwrite our changes on exit.
"""
import os, shutil, sqlite3, struct, subprocess, sys, time

from . import paths

GRID = "BeatGrid-2.0"


def db_candidates():
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return [os.path.join(home, "Library/Containers/org.mixxx.mixxx/Data/Library/"
                                   "Application Support/Mixxx/mixxxdb.sqlite"),
                os.path.join(home, "Library/Application Support/Mixxx/mixxxdb.sqlite")]
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return [os.path.join(local, "Mixxx", "mixxxdb.sqlite")]
    return [os.path.join(home, ".mixxx", "mixxxdb.sqlite"),
            os.path.join(home, ".local/share/mixxx/mixxxdb.sqlite"),
            os.path.join(home, ".var/app/org.mixxx.Mixxx/.mixxx/mixxxdb.sqlite")]


def find_db():
    for p in db_candidates():
        if os.path.isfile(p):
            return p
    return None


def running():
    """Is Mixxx open? Errs on the side of yes if the check itself fails."""
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq mixxx.exe", "/NH"],
                                 capture_output=True, text=True).stdout
            return "mixxx.exe" in out.lower()
        r = subprocess.run(["pgrep", "-ix", "mixxx"], capture_output=True)
        return r.returncode == 0
    except Exception:
        return True


class MixxxOpen(Exception):
    pass


# ------------------------------------------------------------ beatgrid blob
# BeatGrid-2.0 is a two-field protobuf:
#   1: Bpm  { 1: double bpm }
#   2: Beat { 1: int32 frame_position, ... }
def _varint(buf, i):
    shift = val = 0
    while True:
        b = buf[i]
        val |= (b & 0x7F) << shift
        i += 1
        if not b & 0x80:
            return val, i
        shift += 7


def _fields(buf):
    """(field, wire_type, start, end) of each top-level field's payload."""
    i, out = 0, []
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            _, j = _varint(buf, i)
        elif wt == 1:
            j = i + 8
        elif wt == 2:
            n, i = _varint(buf, i)
            j = i + n
        elif wt == 5:
            j = i + 4
        else:
            raise ValueError("unsupported wire type")
        out.append((field, wt, i, j))
        i = j
    return out


def grid_first_beat(blob):
    """Frame position of the grid's first beat, or 0."""
    for f, wt, a, b in _fields(blob):
        if f == 2 and wt == 2:
            for g, wt2, c, d in _fields(blob[a:b]):
                if g == 1 and wt2 == 0:
                    v, _ = _varint(blob[a:b], c)
                    # int32: negatives are sign-extended to 64 bits on the wire
                    return v - (1 << 64) if v >= 1 << 63 else v
    return 0


def _enc_varint(v):
    if v < 0:
        v += 1 << 64
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


def grid_blob(blob, bpm, first_beat):
    """The same grid at a new tempo and first beat; other fields kept as they were."""
    out = bytearray()
    bpm_msg = b"\x09" + struct.pack("<d", float(bpm))
    wrote_bpm = wrote_beat = False
    for f, wt, a, b in _fields(blob):
        if f == 1 and wt == 2:
            out += b"\x0a" + _enc_varint(len(bpm_msg)) + bpm_msg
            wrote_bpm = True
        elif f == 2 and wt == 2:
            inner = bytearray(b"\x08" + _enc_varint(int(first_beat)))
            sub = blob[a:b]
            for g, wt2, c, d in _fields(sub):
                if g != 1:
                    inner += _raw_field(sub, g, wt2, c, d)
            out += b"\x12" + _enc_varint(len(inner)) + inner
            wrote_beat = True
        else:
            out += _raw_field(blob, f, wt, a, b)
    if not wrote_bpm:
        out += b"\x0a" + _enc_varint(len(bpm_msg)) + bpm_msg
    if not wrote_beat:
        inner = b"\x08" + _enc_varint(int(first_beat))
        out += b"\x12" + _enc_varint(len(inner)) + inner
    return bytes(out)


def _raw_field(buf, f, wt, a, b):
    head = _enc_varint((f << 3) | wt)
    if wt == 2:
        head += _enc_varint(b - a)
    return head + bytes(buf[a:b])


# ------------------------------------------------------------ library
class Library:
    """Read-only view of Mixxx's tracks, keyed by file path."""

    def __init__(self, db):
        self.db = db
        self.by_path = {}
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for row in con.execute(
                    "SELECT l.id, tl.location, l.bpm, l.bpm_lock, l.beats_version, "
                    "l.beats, l.samplerate FROM library l "
                    "JOIN track_locations tl ON tl.id = l.location "
                    "WHERE l.mixxx_deleted = 0 AND tl.fs_deleted = 0"):
                tid, loc, bpm, lock, ver, beats, sr = row
                if not loc:
                    continue
                self.by_path[_norm(loc)] = {
                    "id": tid, "bpm": float(bpm or 0), "locked": bool(lock),
                    "version": ver, "beats": bytes(beats) if beats else None,
                    "samplerate": int(sr or 0)}
        finally:
            con.close()

    def get(self, path):
        return self.by_path.get(_norm(path)) or self.by_path.get(_norm(os.path.realpath(path)))

    @staticmethod
    def editable(t):
        """Only plain grids can be rescaled. A locked BPM was set on purpose."""
        return bool(t and t["version"] == GRID and t["beats"] and not t["locked"]
                    and t["samplerate"])


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


def backup(db):
    """A dated copy next to Sortero's journals, before the first write."""
    dst = os.path.join(paths.journals_dir(),
                       time.strftime("mixxxdb-%Y%m%d-%H%M%S.sqlite"))
    shutil.copy2(db, dst)
    return dst


def write(db, changes):
    """changes: [(track_id, bpm, beats_blob, locked)]. All or nothing."""
    if running():
        raise MixxxOpen("Quit Mixxx first. It keeps its library in memory and would "
                        "overwrite these changes when it closes.")
    con = sqlite3.connect(db)
    try:
        with con:
            for tid, bpm, beats, locked in changes:
                con.execute("UPDATE library SET bpm = ?, beats = ?, bpm_lock = ? "
                            "WHERE id = ?", (bpm, beats, int(bool(locked)), tid))
    finally:
        con.close()
