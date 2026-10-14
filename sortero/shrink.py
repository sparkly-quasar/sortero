"""Shrink the library: lossless tracks become MP3 320, tags and all.

For each lossless track (FLAC, WAV, AIFF or Apple Lossless, at any sample rate
or bit depth) Sortero makes an MP3 320 beside it, the same way Gig prep makes
its copies: the SoX resampler to 44.1 kHz where needed, every tag carried over
(Mixed In Key's cue points and beat grid included) and the artwork kept as it
is. Each MP3 is checked before anything moves: it decodes start to finish, is
as long as the original, and reads back the same artist, title, key and BPM.

Only then is the original moved to an archive folder outside the collection,
in the same folders it had, and the MP3 takes its place under the same name.
Sortero's playlists follow, and Mixxx's library is pointed at the MP3s with its
cues and grid kept (mixxx.relink). The batch is one History entry, so all of
it can be undone, until the archive is deleted (delete_archive), which Sortero
offers afterwards and which locks that entry.

MP3s are never touched, and nor are other lossy files: AAC, Ogg Vorbis or Opus
re-encoded to MP3 loses quality and often ends up bigger.
"""
import concurrent.futures, os, shutil, time
from dataclasses import dataclass, field

from . import journal as journal_mod, playlists, tagio
from .gig import convert, ffmpeg, tagcopy
from .gig.probe import ProbeError, probe

KIND = "shrink"
ARCHIVE_SUFFIX = " - Originals"
LOSSLESS = {"flac", "alac", "wav", "aiff"}
MAYBE_LOSSLESS = {".flac", ".fla", ".wav", ".aif", ".aiff", ".m4a"}   # .m4a: ALAC or AAC
BYTES_PER_SECOND = 320_000 // 8
CONVERT, SKIP, DONE, FAILED = "convert", "skip", "done", "failed"
LENGTH_SLACK = 0.5      # seconds an MP3 may differ from its original (encoder padding)
CHECKED_TAGS = ("artist", "title", "key", "bpm")


class ArchiveError(Exception):
    pass


@dataclass
class Item:
    src: str
    rel: str
    info: object = None
    mp3: str = ""
    archive: str = ""
    size: int = 0
    estimate: int = 0
    status: str = CONVERT
    why: str = ""
    notes: list = field(default_factory=list)


@dataclass
class Result:
    items: list
    saved: int = 0                  # bytes the collection got smaller by
    journal: str = None
    mixxx: int = 0                  # tracks relinked in Mixxx
    mixxx_notes: list = field(default_factory=list)
    playlists: int = 0              # playlist entries pointed at the MP3s

    @property
    def done(self):
        return [it for it in self.items if it.status == DONE]

    @property
    def failed(self):
        return [it for it in self.items if it.status == FAILED]


def default_archive(root):
    root = os.path.normpath(os.path.abspath(root))
    return root + ARCHIVE_SUFFIX


def archive_dir(root, chosen=""):
    """Where originals go. Never inside the collection, where they'd be scanned
    as tracks again, and not one that contains it."""
    arc = os.path.normpath(os.path.abspath(chosen or default_archive(root)))
    root = os.path.normpath(os.path.abspath(root))
    if os.path.commonpath([arc, root]) in (arc, root):
        raise ArchiveError("The archive has to be a folder outside your collection, and "
                           "not one that contains it.")
    return arc


def estimate(info):
    return int((info.extras.get("duration") or 0) * BYTES_PER_SECOND)


# -------------------------------------------------------------------- plan
def candidates(recs):
    """The tracks worth looking at: not protected, and named like lossless audio."""
    return [r for r in recs if not r.protected
            and os.path.splitext(r.path)[1].lower() in MAYBE_LOSSLESS]


def plan(root, recs, archive, progress=None):
    """An Item for every lossless track among recs. Others are left out entirely."""
    recs = candidates(recs)
    out = []
    for i, r in enumerate(recs, 1):
        if progress:
            progress(i, len(recs))
        try:
            info = probe(r.path)
        except ProbeError:
            continue                # not readable as audio; nothing to shrink
        if info.format not in LOSSLESS:
            continue                # an AAC in .m4a: lossy already
        rel = os.path.relpath(r.path, root)
        it = Item(r.path, rel, info, os.path.splitext(r.path)[0] + ".mp3",
                  os.path.join(archive, rel), _size(r.path), estimate(info))
        if os.path.exists(it.mp3):
            it.status, it.why = SKIP, "an MP3 with the same name is already beside it"
        elif os.path.exists(it.archive):
            it.status, it.why = SKIP, "the archive already has a file at its place"
        out.append(it)
    return out


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def totals(items):
    """(files, bytes now, bytes after) for the ones that will be converted."""
    todo = [it for it in items if it.status == CONVERT]
    return len(todo), sum(it.size for it in todo), sum(it.estimate for it in todo)


# --------------------------------------------------------------------- run
def _tags(path):
    t = tagio.Track(path)
    return {f: (t.get(f) or "").strip() for f in CHECKED_TAGS} if t.ok else None


def _decode_error(path):
    r = ffmpeg.run([ffmpeg.require("ffmpeg"), "-v", "error", "-xerror", "-nostdin", "-i", path,
                    "-map", "0:a:0", "-f", "null", "-"])
    msg = r.stderr.decode("utf-8", "replace").strip()
    if r.returncode != 0 or msg:
        return msg.splitlines()[0] if msg else "ffmpeg stopped part way"
    return ""


def check(src, mp3, info):
    """'' if the MP3 can replace src, else why not."""
    err = _decode_error(mp3)
    if err:
        return f"the MP3 didn't decode cleanly ({err})"
    try:
        got = probe(mp3)
    except ProbeError as e:
        return f"the MP3 can't be read ({e})"
    want = info.extras.get("duration") or 0
    have = got.extras.get("duration") or 0
    if want and abs(want - have) > LENGTH_SLACK:
        return f"the MP3 is {have:.1f} s long and the original {want:.1f} s"
    before, after = _tags(src), _tags(mp3)
    if before is not None:
        if after is None:
            return "the MP3's tags can't be read back"
        lost = [f for f in CHECKED_TAGS if before[f] and before[f] != after[f]]
        if lost:
            return f"the MP3's {', '.join(lost)} didn't come across"
    return ""


def _encode(it, soxr):
    """Make and check the MP3 beside the original, under a temporary name."""
    folder = os.path.dirname(it.mp3)
    tmp = os.path.join(folder, f".sortero-shrink-{os.getpid()}-{os.path.basename(it.mp3)}")
    try:
        r = ffmpeg.run(convert.ffmpeg_args(it.src, tmp, it.info, convert.MP3, soxr))
        if r.returncode != 0:
            msg = r.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(f"ffmpeg stopped with “{msg[-1]}”" if msg else "ffmpeg stopped")
        it.notes += tagcopy.copy(it.src, tmp, None)
        why = check(it.src, tmp, it.info)
        if why:
            raise RuntimeError(why)
        return tmp
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def run(root, items, archive, mixxx_db=None, progress=None, log=print, workers=None):
    """Convert, check, archive and relink. Returns a Result; the History entry is
    saved as soon as anything has changed, even if a later file fails."""
    todo = [it for it in items if it.status == CONVERT]
    res = Result(items)
    if not todo:
        return res
    ffmpeg.require("ffmpeg")
    if not ffmpeg.has_lame():
        raise ffmpeg.Missing("This ffmpeg can't write MP3 (it was built without LAME). "
                             "Install a full ffmpeg to shrink the library.")
    soxr = ffmpeg.has_soxr()
    j = journal_mod.Journal(KIND, root)
    j.extra["archive"] = archive
    moved = {}
    workers = workers or max(1, min(4, os.cpu_count() or 1))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_encode, it, soxr): it for it in todo}
            for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                it = futures[fut]
                try:
                    tmp = fut.result()
                    # The MP3 is good: now, and only now, the original moves.
                    os.makedirs(os.path.dirname(it.archive), exist_ok=True)
                    shutil.move(it.src, it.archive)
                    j.moved(it.src, it.archive)
                    os.replace(tmp, it.mp3)
                    j.created(it.mp3)
                    it.status = DONE
                    res.saved += it.size - _size(it.mp3)
                    moved[it.src] = it.mp3
                except Exception as e:
                    it.status, it.why = FAILED, str(e)
                    log(f"  {it.rel}: {e}")
                if progress:
                    progress(i, len(todo))
        if moved:
            res.playlists = playlists.remap(root, moved, journal=j)
            if mixxx_db:
                _relink(res, j, mixxx_db, todo)
    finally:
        res.journal = j.save()
    return res


def _relink(res, j, db, todo):
    from . import mixxx
    moves = [(it.src, it.mp3, convert.RATE) for it in todo if it.status == DONE]
    try:
        mixxx.backup(db)
        changes, res.mixxx_notes = mixxx.relink(db, moves)
    except mixxx.MixxxOpen as e:
        res.mixxx_notes = [str(e) + " Mixxx still points at the originals; shrinking "
                           "again won't redo the files, so relink them in Mixxx by hand."]
        return
    except Exception as e:
        res.mixxx_notes = [f"Mixxx's library couldn't be updated ({e})."]
        return
    for c in changes:
        j.mixxx_relinked(db, c)
    res.mixxx = len(changes)


# ------------------------------------------------------------ the archive
@dataclass
class Archive:
    """A shrink batch whose originals are still in its archive."""
    journal: str
    when: float
    folder: str
    files: list             # [(archived original, the MP3 that replaced it)]
    bytes: int


def archives():
    """Batches with originals still archived, newest first."""
    out = []
    for d in journal_mod.list_journals():
        if d.get("kind") != KIND or d.get("locked"):
            continue
        files = _pairs(d["entries"])
        files = [(a, m) for a, m in files if os.path.isfile(a)]
        if files:
            out.append(Archive(d["_file"], d.get("finished") or d.get("started") or 0,
                               d.get("archive", ""), files, sum(_size(a) for a, _ in files)))
    return out


def _pairs(entries):
    """(archived original, MP3) for each file a shrink batch replaced."""
    out = []
    for e, nxt in zip(entries, entries[1:]):
        if e["op"] == "move" and nxt["op"] == "create":
            out.append((e["dst"], nxt["path"]))
    return out


def delete_archive(arc, log=print):
    """Delete a batch's archived originals for good. Only those whose MP3 is still
    there: an original whose MP3 has gone is kept. Locks the History entry,
    since it can't be undone after this. Returns (deleted, kept, bytes freed)."""
    deleted = kept = freed = 0
    for original, mp3 in arc.files:
        if not os.path.isfile(mp3):
            kept += 1
            log(f"  kept {original}: its MP3 isn't there any more")
            continue
        size = _size(original)
        try:
            os.remove(original)
            deleted += 1
            freed += size
        except OSError as e:
            kept += 1
            log(f"  couldn't delete {original}: {e.strerror}")
    if arc.folder and os.path.isdir(arc.folder):
        journal_mod.prune_empty(arc.folder)
        try:
            os.rmdir(arc.folder)        # only if it's empty now
        except OSError:
            pass
    if deleted:
        journal_mod.lock(arc.journal, "the originals were deleted on "
                         + time.strftime("%d %b %Y"))
    return deleted, kept, freed
