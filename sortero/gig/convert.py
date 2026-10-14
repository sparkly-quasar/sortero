"""Gig-safe copies of the tracks a gig needs, without ever touching an original.

For each track in the chosen playlists:

  ready      it already plays on the chosen players, so the original is used
             as it is and nothing is written
  converted  it doesn't, so a copy is made in the gig library, a separate
             folder that mirrors the collection's layout
  reused     a copy made earlier from the very same audio is still good
  failed     it couldn't be read or converted; the reason says why

Copies are MP3 320 kbps CBR at 44.1 kHz by default, or 16-bit 44.1 kHz AIFF.
A file that is already lossy (an MP3 at 32 kHz, say, or HE-AAC) never gets a
second lossy encode: it is decoded to AIFF instead, which loses nothing more,
and the report says so.

Resampling uses the SoX resampler when ffmpeg has it. Reducing to 16 bit adds
triangular (TPDF) dither. MP3 encoding is fed floating point straight from the
resampler, so there's only ever one rounding step.

Copies are cached by the original's content hash, so a re-run only converts
what changed, and a track that was moved or renamed is not converted again.
"""
import concurrent.futures, hashlib, json, os, shutil
from dataclasses import dataclass, field

from .. import paths
from . import ffmpeg, profiles, tagcopy
from .probe import ProbeError, is_lossless, probe

MP3, AIFF = "mp3", "aiff"
TARGET_LABELS = {MP3: "MP3 320 kbps", AIFF: "AIFF 16-bit"}
RATE = 44100
# Bump when copies come out differently, so the cache stops trusting old ones.
VERSION = 1

READY, CONVERT, CONVERTED, REUSED, FAILED = "ready", "convert", "converted", "reused", "failed"


class LibraryError(Exception):
    pass


@dataclass
class Item:
    src: str
    rel: str
    status: str
    info: profiles.AudioInfo = None
    target: str = None          # MP3 or AIFF, for items that need a copy
    out: str = None
    why: list = field(default_factory=list)      # why it needs a copy, or why it failed
    notes: list = field(default_factory=list)    # worth knowing, not a failure

    @property
    def playable(self):
        """The file a player should be given: the copy, or the original as it is."""
        return self.out if self.status in (CONVERTED, REUSED) else (
            self.src if self.status == READY else None)


# ----------------------------------------------------------------- library
def default_library(root):
    root = os.path.normpath(root)
    return os.path.join(os.path.dirname(root), os.path.basename(root) + " - Gig Library")


def library_dir(root, chosen=""):
    """Where copies go. Never inside the collection, or every copy would read as a duplicate."""
    lib = os.path.normpath(os.path.abspath(chosen or default_library(root)))
    root = os.path.normpath(os.path.abspath(root))
    if os.path.commonpath([lib, root]) in (lib, root):
        raise LibraryError("The gig library has to be a folder outside your collection, "
                           "and not one that contains it.")
    return lib


def playlist_paths(sets):
    """Every track in these [(name, [paths])] playlists, in order, each once."""
    seen, out = set(), []
    for _, tracks in sets:
        for p in tracks:
            key = os.path.normcase(p)
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def _mirror(root, lib, src, target):
    rel = os.path.relpath(src, root)
    if rel.startswith(os.pardir):
        rel = os.path.join("_Outside the collection", os.path.basename(src))
    stem = os.path.splitext(rel)[0]
    return rel, os.path.join(lib, stem + ("." + target if target == MP3 else ".aiff"))


# -------------------------------------------------------------------- plan
def _decide(profile, info):
    """(needs a copy?, reasons). Players with no known format table use the universal one."""
    use = profile if profile.formats_known else profiles.UNIVERSAL
    problems = profiles.check_audio(use, info)
    return bool(problems), [p.text for p in problems]


def plan(root, sources, lib, profile, target=MP3, progress=None):
    """Probe every source and decide what each needs. Writes nothing."""
    items = []
    for i, src in enumerate(sources):
        if progress:
            progress(i, len(sources))
        rel = os.path.relpath(src, root)
        if not os.path.isfile(src):
            items.append(Item(src, rel, FAILED, why=["The playlist points at a file that "
                                                     "isn't there."]))
            continue
        try:
            info = probe(src)
        except ProbeError as e:
            items.append(Item(src, rel, FAILED, why=[f"Couldn't read it: {e}."]))
            continue
        needs, why = _decide(profile, info)
        if not needs:
            items.append(Item(src, rel, READY, info))
            continue
        item_target = target
        notes = []
        if not is_lossless(info):
            item_target = AIFF
            notes.append("It's already a lossy file, so it was decoded to AIFF rather "
                         "than encoded to lossy again. Nothing more is lost, but the "
                         "copy is bigger. Consider getting a better copy of this track.")
        rel, out = _mirror(root, lib, src, item_target)
        items.append(Item(src, rel, CONVERT, info, item_target, out, why, notes))
    _resolve_clashes(items)
    if progress:
        progress(len(sources), len(sources))
    return items


def _resolve_clashes(items):
    """Track.flac and Track.wav in one folder would both become Track.mp3."""
    by_out = {}
    for it in items:
        if it.out:
            by_out.setdefault(os.path.normcase(it.out), []).append(it)
    for group in by_out.values():
        if len(group) > 1:
            for it in group:
                stem, ext = os.path.splitext(it.out)
                it.out = f"{stem} ({os.path.splitext(it.src)[1].lstrip('.').lower()}){ext}"


# ------------------------------------------------------------------- cache
class Cache:
    """Content hashes of originals, and the copies made from them."""

    def __init__(self, path=None):
        self.path = path or os.path.join(paths.data_dir(), "gig-cache.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        self.hashes = data.get("hashes", {})
        self.copies = data.get("copies", {})

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"hashes": self.hashes, "copies": self.copies}, fh)
        os.replace(tmp, self.path)

    def digest(self, path):
        st = os.stat(path)
        known = self.hashes.get(path)
        if known and known[0] == st.st_size and known[1] == st.st_mtime_ns:
            return known[2]
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        self.hashes[path] = [st.st_size, st.st_mtime_ns, h.hexdigest()]
        return h.hexdigest()

    @staticmethod
    def key(digest, target, art_px):
        return f"{digest}:{target}:{art_px}:{VERSION}"

    def find(self, key):
        """A copy made earlier for this key that is still exactly as it was written."""
        c = self.copies.get(key)
        if not c:
            return None
        try:
            st = os.stat(c["out"])
        except OSError:
            return None
        if st.st_size != c["size"] or st.st_mtime_ns != c["mtime_ns"]:
            return None
        return c

    def remember(self, key, out, notes):
        st = os.stat(out)
        self.copies[key] = {"out": out, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                            "notes": notes}


# --------------------------------------------------------------------- run
def _filters(info, target, soxr):
    """The aresample filter, or None when the audio can go straight to the encoder."""
    resample = info.rate != RATE
    # Lossy formats decode to floating point, so they count as deeper than 16 bit.
    deeper = info.depth is None or info.depth > 16 or info.extras.get("float")
    opts = []
    if resample:
        opts.append(f"out_sample_rate={RATE}")
        if soxr:
            opts += ["resampler=soxr", "precision=28"]
    if target == AIFF and (resample or deeper):
        opts += ["out_sample_fmt=s16", "dither_method=triangular"]
    elif target == MP3 and resample:
        opts.append("out_sample_fmt=fltp")
    return "aresample=" + ":".join(opts) if opts else None


def ffmpeg_args(src, dst, info, target, soxr):
    args = [ffmpeg.require("ffmpeg"), "-v", "error", "-nostdin", "-y", "-i", src,
            "-map", "0:a:0", "-map_metadata", "-1", "-vn"]
    af = _filters(info, target, soxr)
    if af:
        args += ["-af", af]
    if info.extras.get("channels", 2) > 2:
        args += ["-ac", "2"]
    if target == MP3:
        args += ["-c:a", "libmp3lame", "-b:a", "320k", "-ar", str(RATE),
                 "-id3v2_version", "0", "-write_id3v1", "0", "-f", "mp3"]
    else:
        args += ["-c:a", "pcm_s16be", "-ar", str(RATE), "-write_id3v2", "0", "-f", "aiff"]
    return args + [dst]


def _convert_one(item, art_px, soxr, check_profile):
    """Make one copy. Returns notes. Raises with a reason the user can read."""
    folder = os.path.dirname(item.out)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".sortero-tmp-{os.getpid()}-{os.path.basename(item.out)}")
    try:
        r = ffmpeg.run(ffmpeg_args(item.src, tmp, item.info, item.target, soxr))
        if r.returncode != 0:
            msg = r.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(f"ffmpeg stopped with “{msg[-1]}”" if msg else "ffmpeg stopped")
        notes = tagcopy.copy(item.src, tmp, art_px)
        problems = profiles.check_audio(check_profile, probe(tmp))
        if problems:
            raise RuntimeError("the copy still wouldn't play: " + problems[0].text)
        os.replace(tmp, item.out)
        return notes
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def run(items, profile, cache=None, progress=None, log=print, workers=None):
    """Make every copy the plan calls for. Updates the items in place and returns them."""
    todo = [it for it in items if it.status == CONVERT]
    if todo:
        ffmpeg.require("ffmpeg")
        if not ffmpeg.has_lame():
            raise ffmpeg.Missing("This ffmpeg can't write MP3 (it was built without "
                                 "LAME). Choose AIFF copies, or install a full ffmpeg.")
    cache = cache or Cache()
    check = profile if profile.formats_known else profiles.UNIVERSAL
    art_px = check.artwork_px or profiles.UNIVERSAL.artwork_px
    soxr = bool(todo) and ffmpeg.has_soxr()
    if todo and not soxr:
        log("ffmpeg has no SoX resampler here; using its built-in one.")

    fresh = []
    for it in todo:
        try:
            key = Cache.key(cache.digest(it.src), it.target, art_px)
        except OSError as e:
            it.status, it.why = FAILED, [f"Couldn't read it: {e.strerror}."]
            continue
        hit = cache.find(key)
        if hit:
            if os.path.normcase(hit["out"]) != os.path.normcase(it.out):
                os.makedirs(os.path.dirname(it.out), exist_ok=True)
                shutil.copyfile(hit["out"], it.out)
                cache.remember(key, it.out, hit["notes"])
            it.status = REUSED
            it.notes += hit["notes"]
        else:
            fresh.append((it, key))

    total = len(todo)
    done = total - len(fresh)
    if progress:
        progress(done, total)
    workers = workers or max(1, min(4, os.cpu_count() or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_convert_one, it, art_px, soxr, check): (it, key)
                   for it, key in fresh}
        for fut in concurrent.futures.as_completed(futures):
            it, key = futures[fut]
            try:
                it.notes += fut.result()
                it.status = CONVERTED
                cache.remember(key, it.out, it.notes)
            except Exception as e:
                it.status, it.why = FAILED, [f"Conversion failed: {e}."]
                log(f"  ! {it.rel}: {e}")
            done += 1
            if progress:
                progress(done, total)
    cache.save()
    return items


# ------------------------------------------------------------------ report
def counts(items):
    c = {READY: 0, CONVERTED: 0, REUSED: 0, FAILED: 0, CONVERT: 0}
    for it in items:
        c[it.status] += 1
    return c


def report_lines(items):
    """The report as plain lines: what happened, then anything that needs a look."""
    c = counts(items)
    lines = [f"{c[READY]} already play as they are · {c[CONVERTED]} converted · "
             f"{c[REUSED]} reused from last time · {c[FAILED]} failed"]
    for it in items:
        if it.status == FAILED:
            lines.append(f"FAILED  {it.rel}: {' '.join(it.why)}")
    for it in items:
        if it.status in (CONVERTED, REUSED):
            lines.append(f"{it.status.upper():9} {it.rel} → {TARGET_LABELS[it.target]}: "
                         f"{it.why[0] if it.why else ''}")
            lines += [f"          {n}" for n in it.notes]
    return lines
