"""AIFF and WAV files with an ID3 tag glued to the front, and putting them right.

AIFF and WAV keep their ID3 tag in a chunk inside the file. Sortero used to
write a bare ID3 tag to the path instead, which puts it in front of the
header: the file then starts "ID3" where every player expects "FORM" or
"RIFF". Forgiving decoders skip it. Stricter ones refuse the file, and to
mutagen it no longer reads as AIFF or WAV at all, so its length and the tags
that rekordbox or Mixed In Key wrote in the chunk went missing too.

Repair moves the stray tag into the chunk, where it belongs, and sets the
damaged original aside in _Quarantine, so History can put it back.
"""
import io, os, shutil

import mutagen
from mutagen.aiff import AIFF
from mutagen.wave import WAVE
from mutagen.id3 import ID3

from .journal import Journal

IFF_EXTS = {".aif", ".aiff", ".wav"}
QUARANTINE_SUB = "damaged headers"
# A stray tag this far in is not a tag: stop looking rather than read on.
MAX_STRAY = 64 * 1024 * 1024
# Frames Sortero writes by clearing every frame of that type and adding one.
# A stray tag carrying one of these is Sortero's later word on it, so it
# replaces the chunk's outright; anything else is merged frame by frame.
REPLACED = {"TKEY", "TIT1", "TCON", "TPE1", "TIT2", "TBPM", "TALB", "COMM"}


class RepairError(Exception):
    pass


def _is_iff(head):
    return ((head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"))
            or (head[:4] in (b"RIFF", b"RF64") and head[8:12] == b"WAVE"))


def _segments(fh):
    """(start, end) of each ID3 tag stacked in front of an AIFF/WAV header.

    Empty when the file starts with its header, or when what follows the tags
    isn't an AIFF or WAV header after all.
    """
    out, off = [], 0
    while off < MAX_STRAY:
        fh.seek(off)
        head = fh.read(12)
        if len(head) >= 10 and head[:3] == b"ID3":
            b = head[6:10]
            size = (b[0] & 0x7F) << 21 | (b[1] & 0x7F) << 14 | (b[2] & 0x7F) << 7 | b[3] & 0x7F
            end = off + 10 + size + (10 if head[5] & 0x10 else 0)   # 0x10: footer
            out.append((off, end))
            off = end
            continue
        return out if out and _is_iff(head) else []
    return []


def stray_id3(path):
    """How many bytes of ID3 tag sit in front of this AIFF or WAV header. 0 if none."""
    if os.path.splitext(path)[1].lower() not in IFF_EXTS:
        return 0
    try:
        with open(path, "rb") as fh:
            segs = _segments(fh)
    except OSError:
        return 0
    return segs[-1][1] if segs else 0


def _overlay(chunk, stray):
    for fid in {f.FrameID for f in stray.values()} & REPLACED:
        chunk.delall(fid)
    for f in stray.values():
        if f.FrameID in REPLACED:
            chunk.add(f)
        else:
            chunk[f.HashKey] = f


def _quarantine_path(root, path):
    rel = os.path.relpath(path, root).replace(os.sep, "__")
    dest = os.path.join(root, "_Quarantine", QUARANTINE_SUB, rel)
    stem, ext = os.path.splitext(dest)
    i = 1
    while os.path.exists(dest):
        i += 1
        dest = f"{stem} ({i}){ext}"
    return dest


def repair_one(root, path, journal):
    """Rewrite one damaged file with its tags in the chunk. False if it wasn't damaged."""
    with open(path, "rb") as fh:
        segs = _segments(fh)
        if not segs:
            return False
        strays = []
        for start, end in segs:
            fh.seek(start)
            strays.append(ID3(fileobj=io.BytesIO(fh.read(end - start))))
        body_at = segs[-1][1]

    folder, name = os.path.split(path)
    tmp = os.path.join(folder, f".sortero-repair-{os.getpid()}-{name}")
    try:
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            src.seek(body_at)
            shutil.copyfileobj(src, dst, 1 << 20)
        f = mutagen.File(tmp)
        if not isinstance(f, (AIFF, WAVE)):
            raise RepairError("still doesn't read as AIFF or WAV without the stray tag")
        length = f.info.length
        if f.tags is None:
            f.add_tags()
        # The first tag in the file is the one Sortero read and rewrote, so it
        # is the newest; lay the oldest down first and let it win.
        for tag in reversed(strays):
            _overlay(f.tags, tag)
        f.save(v2_version=3)
        check = mutagen.File(tmp)
        if not isinstance(check, (AIFF, WAVE)) or abs(check.info.length - length) > 1e-6:
            raise RepairError("the repaired copy didn't read back the same")

        dest = _quarantine_path(root, path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(path, dest)
        try:
            os.replace(tmp, path)
        except OSError:
            shutil.move(dest, path)
            raise
        journal.moved(path, dest)
        journal.created(path)
        return True
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def repair(root, paths, log=print, progress=None):
    """Repair every damaged file in paths. Returns (journal path, repaired, failed)."""
    j = Journal("repair-headers", root)
    n = failed = 0
    for i, p in enumerate(paths):
        if progress:
            progress(i, len(paths))
        try:
            n += repair_one(root, p, j)
        except Exception as e:
            failed += 1
            log(f"  ! {os.path.basename(p)}: {e}")
    if progress:
        progress(len(paths), len(paths))
    path = j.save()
    log(f"repaired {n} AIFF/WAV headers ({failed} failed) | journal: {path}")
    return path, n, failed
