"""The plain-folder backup stick: each playlist a numbered folder, in set order.

    01 Warmup/01 Artist - Title.mp3
    01 Warmup/02 Artist - Title.mp3
    02 Peak/01 Artist - Title.mp3

A player in folder-browse mode, or a laptop running Traktor, can find the
next track fast if rekordbox fails. Files are numbered and also written in
set order: whether a player sorts a folder by name or lists it in the order
it was written isn't documented, so both give the same order.

Sortero keeps a list on the stick of what it wrote (MANIFEST). A second run
brings the stick up to date: unchanged files are left alone, files from an
old set that aren't in this one are removed, and anything Sortero didn't
write is never touched. The same list lets the stick check compare every
file with the original it came from.

Files are copied without macOS metadata, so no "._" files are made, and any
already there are cleared (sweep_appledouble).
"""
import json, os, shutil, time, unicodedata
from dataclasses import dataclass, field

from .verify import FAT32_MAX, FAT_ILLEGAL, RESERVED

MANIFEST = ".sortero-backup.json"
TMP = ".sortero-tmp-"
APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"
MAX_NAME = 255


class BackupError(Exception):
    pass


@dataclass
class Result:
    written: list = field(default_factory=list)     # stick-relative paths
    unchanged: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)     # (what, why)
    appledouble: int = 0

    def lines(self):
        out = [f"{len(self.written):,} written · {len(self.unchanged):,} already there · "
               f"{len(self.removed):,} old ones removed · {len(self.skipped):,} skipped"]
        out += [f"SKIPPED  {what}: {why}" for what, why in self.skipped]
        if self.appledouble:
            out.append(f"Removed {self.appledouble:,} macOS “._” files.")
        return out


# ------------------------------------------------------------------- names
def fat_name(name):
    """A name a FAT32 stick can hold: illegal characters swapped, no trailing dots.

    A "/" becomes "-" too: in a tag ("OFF / GRID") it would otherwise make a folder.
    Accents are stored whole (NFC), as Windows and rekordbox write them, so the name
    in the library matches the name on the stick whichever way the source file had it."""
    name = unicodedata.normalize("NFC", name).replace("/", "-")
    stem, ext = os.path.splitext(name)
    stem = FAT_ILLEGAL.sub("-", stem).rstrip(" .") or "Untitled"
    if stem.upper() in RESERVED:
        stem += "_"
    while len((stem + ext).encode("utf-16-le")) // 2 > MAX_NAME:
        stem = stem[:-1].rstrip(" .")
    return stem + ext


def layout(sets):
    """{stick path: source} for [(playlist name, [playable paths in order])].

    Paths use "/" whatever the platform, as players and the manifest see them.
    """
    width = max(2, len(str(len(sets))))
    out = {}
    for i, (name, tracks) in enumerate(sets, 1):
        folder = fat_name(f"{i:0{width}d} {name}")
        tw = max(2, len(str(len(tracks))))
        for j, src in enumerate(tracks, 1):
            out[f"{folder}/{fat_name(f'{j:0{tw}d} {os.path.basename(src)}')}"] = src
    return out


# ---------------------------------------------------------------- manifest
def read_manifest(stick):
    try:
        with open(os.path.join(stick, MANIFEST), encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("files", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_manifest(stick, files):
    path = os.path.join(stick, MANIFEST)
    tmp = os.path.join(stick, TMP + MANIFEST.lstrip("."))
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"written_by": "Sortero", "when": time.strftime("%Y-%m-%d %H:%M"),
                   "files": files}, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def sources(stick):
    """{stick path: original} from the manifest, for the stick check's checksums."""
    return {rel: f["src"] for rel, f in read_manifest(stick).items() if f.get("src")}


# -------------------------------------------------------------------- plan
@dataclass
class Plan:
    stick: str
    want: dict                  # stick path -> source
    copy: list                  # stick paths to write
    keep: list                  # stick paths already right
    remove: list                # stick paths an earlier backup wrote, no longer wanted
    blocked: list               # (stick path, why) that can't be written
    bytes: int = 0              # to be written

    @property
    def folders(self):
        return sorted({rel.split("/")[0] for rel in self.want})


def plan(stick, sets):
    if not os.path.isdir(stick):
        raise BackupError(f"{stick} isn't there. Is the stick plugged in?")
    want = layout(sets)
    before = read_manifest(stick)
    copy, keep, blocked, total = [], [], [], 0
    for rel, src in want.items():
        dest = os.path.join(stick, *rel.split("/"))
        try:
            size = os.path.getsize(src)
        except OSError:
            blocked.append((rel, "the original isn't there any more"))
            continue
        if size > FAT32_MAX:
            blocked.append((rel, "it's over 4 GB, which FAT32 can't hold"))
            continue
        old = before.get(rel)
        if os.path.exists(dest):
            if not old:
                blocked.append((rel, "a file Sortero didn't put there is in the way"))
                continue
            if (old.get("src") == src and old.get("size") == size
                    and old.get("src_mtime") == os.stat(src).st_mtime_ns
                    and os.path.getsize(dest) == size):
                keep.append(rel)
                continue
        copy.append(rel)
        total += size
    remove = [rel for rel in before if rel not in want
              and os.path.exists(os.path.join(stick, *rel.split("/")))]
    return Plan(stick, want, copy, keep, remove, blocked, total)


def free_space(path):
    return shutil.disk_usage(path).free


# --------------------------------------------------------------------- run
def _copy(src, dest):
    """Copy data only (no macOS metadata, so no "._" file), finished before it's named."""
    folder = os.path.dirname(dest)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, TMP + os.path.basename(dest))
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
            fo.flush()
            os.fsync(fo.fileno())
        if os.path.getsize(tmp) != os.path.getsize(src):
            raise OSError("the copy came out a different size")
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def sweep_appledouble(stick):
    """Remove macOS "._" files: only real ones, recognised by their header.

    This used to run macOS's dot_clean first. dot_clean removes every file
    whose name starts with "._", including a real track that happens to be
    named that way, and it left nothing for the count below to report.
    """
    n = 0
    for dirpath, dirnames, filenames in os.walk(stick):
        for fn in filenames:
            if not fn.startswith("._"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "rb") as fh:
                    if fh.read(4) != APPLEDOUBLE_MAGIC:
                        continue
                os.remove(p)
                n += 1
            except OSError:
                pass
    return n


def _leftovers(stick):
    """Half-written files from a run that was interrupted."""
    for dirpath, _, filenames in os.walk(stick):
        for fn in filenames:
            if fn.startswith(TMP):
                try:
                    os.remove(os.path.join(dirpath, fn))
                except OSError:
                    pass


def run(p, progress=None, log=print):
    """Bring the stick in line with the plan. Returns a Result."""
    if p.bytes > free_space(p.stick):
        from ..common import human_size
        raise BackupError(f"The stick needs {human_size(p.bytes)} free and has "
                          f"{human_size(free_space(p.stick))}. Free some space, or use a "
                          "bigger stick.")
    _leftovers(p.stick)
    res = Result(unchanged=list(p.keep), skipped=list(p.blocked))
    manifest = {rel: f for rel, f in read_manifest(p.stick).items()
                if rel in p.want and rel not in p.copy}

    for rel in p.remove:
        try:
            os.remove(os.path.join(p.stick, *rel.split("/")))
            res.removed.append(rel)
        except OSError as e:
            log(f"  ! couldn't remove {rel}: {e.strerror}")
    total = len(p.copy)
    for i, rel in enumerate(p.copy):
        if progress:
            progress(i, total)
        src = p.want[rel]
        try:
            _copy(src, os.path.join(p.stick, *rel.split("/")))
        except OSError as e:
            res.skipped.append((rel, f"couldn't write it ({e.strerror or e})"))
            continue
        st = os.stat(src)
        manifest[rel] = {"src": src, "size": st.st_size, "src_mtime": st.st_mtime_ns}
        res.written.append(rel)
        _write_manifest(p.stick, manifest)      # as it goes, so a stopped run is known
    if progress:
        progress(total, total)
    _write_manifest(p.stick, manifest)
    _prune(p.stick, {rel.split("/")[0] for rel in read_manifest(p.stick)} |
           {rel.split("/")[0] for rel in res.removed})
    res.appledouble = sweep_appledouble(p.stick)
    return res


def _prune(stick, folders):
    """Remove folders an earlier backup made that are now empty. Never anything else."""
    for folder in folders:
        path = os.path.join(stick, folder)
        try:
            if os.path.isdir(path) and not os.listdir(path):
                os.rmdir(path)
        except OSError:
            pass


def sets_from(items, chosen):
    """[(playlist, [playable paths])] in set order, from Gig prep's checked tracks
    and the [(playlist, [original paths])] they were checked from.

    Returns (sets, missing): tracks with nothing playable yet are left out and
    named in missing.
    """
    playable = {os.path.normcase(it.src): it.playable for it in items}
    sets, missing = [], []
    for name, originals in chosen:
        tracks = []
        for src in originals:
            p = playable.get(os.path.normcase(src))
            if p:
                tracks.append(p)
            else:
                missing.append((name, src))
        sets.append((name, tracks))
    return sets, missing
