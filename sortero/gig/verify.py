"""Check a mounted stick before a gig, and say plainly whether it's ready.

Six checks, each pass, warn or fail:

  Stick            file system, partition map, partitions (profiles.check_volume)
  rekordbox        export.pdb and exportLibrary.db (OneLibrary) are there and
                   readable, and every track they list is on the stick, the
                   size it was when the library was written
  Formats          every audio file plays on the chosen players
  Decode test      every audio file decodes from start to finish
  Names and sizes  nothing a FAT32 stick or a player can't handle
  Checksums        files match the originals they came from: always on a
                   stick Sortero wrote, otherwise when originals are given

Checking the *main* stick (role=MAIN) adds one more:

  Main stick       a library the chosen players read is on it (rekordbox's for
                   Pioneer, Engine DJ's for Denon; either will do), and
                   tonight's playlists are in the rekordbox one

Any fail means NOT READY. Warnings are listed but don't stop the gig.

Also runs from a terminal:

    python -m sortero.gig.verify /Volumes/GIG [--player cdj-2000] [--quick]
"""
import argparse, concurrent.futures, hashlib, os, re, sys, unicodedata
from dataclasses import dataclass, field

from .. import headers
from . import disks, ffmpeg, onelibrary, pdb, profiles
from .probe import ProbeError, probe

PASS, WARN, FAIL = "pass", profiles.WARN, profiles.FAIL
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".mp4", ".wav", ".aif", ".aiff", ".flac", ".fla",
              ".ogg", ".opus", ".wma", ".alac"}
# Folders operating systems keep on a stick for themselves.
SYSTEM_DIRS = {".Spotlight-V100", ".Trashes", ".fseventsd", ".TemporaryItems",
               "System Volume Information", "$RECYCLE.BIN", ".DocumentRevisions-V100"}
FAT32_MAX = 4 * 1024 ** 3 - 1           # largest file FAT32 can hold
FOLDER_DISPLAY_MAX = 10000              # Pioneer players show no more files than this per folder
# Where Engine DJ keeps the Engine database on a stick ("the Engine Library folder
# of an external storage drive with an Engine database": PRIME guide v5.0.0 p. 43)
ENGINE_DIR = "Engine Library"
FAT_ILLEGAL = re.compile(r'[\x00-\x1f"*:<>?\\|]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}
SHOWN = 4                               # detail lines per check, so it fits on one screen
MAIN, BACKUP = "main", "backup"         # which stick is being checked, when Gig prep knows


@dataclass
class Check:
    title: str
    level: str
    summary: str
    details: list = field(default_factory=list)


@dataclass
class Report:
    stick: str
    profile: profiles.DeckProfile
    checks: list = field(default_factory=list)
    libraries: list = field(default_factory=list)   # libraries found: "rekordbox", "Engine DJ"
    audio: int = 0                                  # audio files on the stick

    @property
    def calm_verdict(self):
        """The verdict as Gig prep shows it: no shouting, and says what's next."""
        if not self.ready:
            n = self.problems
            return f"Not ready yet: {n} {'thing' if n == 1 else 'things'} to fix."
        if self.warnings:
            n = self.warnings
            return f"GIG READY, with {n} {'thing' if n == 1 else 'things'} worth a look."
        return "GIG READY"

    @property
    def first_problem(self):
        c = next((c for c in self.checks if c.level == FAIL), None)
        return f"{c.title}: {c.summary}" if c else ""

    @property
    def problems(self):
        return sum(1 for c in self.checks if c.level == FAIL)

    @property
    def warnings(self):
        return sum(1 for c in self.checks if c.level == WARN)

    @property
    def ready(self):
        return self.problems == 0

    @property
    def verdict(self):
        if not self.ready:
            return f"NOT READY: {self.problems} {'problem' if self.problems == 1 else 'problems'} to fix."
        if self.warnings:
            return (f"GIG READY, with {self.warnings} "
                    f"{'warning' if self.warnings == 1 else 'warnings'} worth a look.")
        return "GIG READY"

    def lines(self, problems_first=False):
        out = [f"Checking {self.stick} for {profiles.where(self.profile)}", ""]
        checks = self.checks
        if problems_first:
            rank = {FAIL: 0, WARN: 1, PASS: 2}
            checks = sorted(checks, key=lambda c: rank[c.level])
        for c in checks:
            out.append(f"{c.level.upper():4}  {c.title}: {c.summary}")
            for d in c.details[:SHOWN]:
                out.append(f"        {d}")
            if len(c.details) > SHOWN:
                out.append(f"        …and {len(c.details) - SHOWN} more")
        return out + ["", self.verdict]


def _plural(n, one, many=None):
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _all(n, one):
    return f"the {one}" if n == 1 else f"both {one}s" if n == 2 else f"all {n:,} {one}s"


# --------------------------------------------------------------- the files
@dataclass
class Entry:
    rel: str            # with "/" separators, as players see it
    path: str
    size: int


def walk(stick):
    """Every file on the stick except what operating systems keep for themselves."""
    out = []
    for dirpath, dirnames, filenames in os.walk(stick):
        dirnames[:] = sorted(d for d in dirnames if d not in SYSTEM_DIRS)
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            out.append(Entry(os.path.relpath(p, stick).replace(os.sep, "/"), p, size))
    return out


def name_key(rel):
    """How a path on the stick is compared: FAT32 ignores case, and an accented letter
    can be stored whole (ë) or as a letter plus accent; macOS may hand back either."""
    return unicodedata.normalize("NFC", rel).lower()


def is_appledouble(rel):
    return os.path.basename(rel).startswith("._")


def is_audio(e):
    return (os.path.splitext(e.rel)[1].lower() in AUDIO_EXTS and not is_appledouble(e.rel)
            and not e.rel.upper().startswith("PIONEER/"))


def name_problems(rel):
    """What a FAT32 stick can't store in this path. Used before copying, too."""
    out = []
    for part in rel.split("/"):
        if FAT_ILLEGAL.search(part):
            out.append(f"{rel}: “{part}” has a character FAT32 can't store")
        elif part != part.rstrip(" ."):
            out.append(f"{rel}: “{part}” ends in a space or a dot, which FAT32 drops")
        elif os.path.splitext(part)[0].upper() in RESERVED:
            out.append(f"{rel}: “{part}” is a name Windows and FAT32 reserve")
        elif len(part.encode("utf-16-le")) // 2 > 255:
            out.append(f"{rel}: a name longer than FAT32's 255 characters")
    return out


def slash_split(rel):
    """For a track rekordbox exported to Contents/<Artist>/<Album>/: the tag whose "/"
    rekordbox made into folders, as (the folders it made, "artist" or "album", the tag),
    or None. "OFF / GRID" arrives as the folders "OFF " and " GRID"."""
    parts = rel.split("/")
    if len(parts) < 3 or parts[0] != "Contents":
        return None
    groups, at = [], 1
    for part in parts[1:-1]:
        if groups and groups[-1][-1].endswith(" "):
            groups[-1].append(part)
        else:
            groups.append([part])
    for tag, g in zip(("artist", "album"), groups):
        at += len(g)
        if len(g) > 1:
            return "/".join(parts[:at]), tag, "/".join(g)
    return None


# ------------------------------------------------------------------ checks
def check_stick(profile, vol):
    if vol.error or not vol.file_system:
        return Check("Stick", WARN, "couldn't tell its file system"
                     + (f" ({vol.error})" if vol.error else "")
                     + f". Make sure it's {profiles.FAT32}.")
    if vol.file_system == disks.FAT:
        return Check("Stick", WARN, "FAT, but the version wasn't reported. Make sure "
                                    f"it's {profiles.FAT32}.")
    parts = vol.partitions or 1
    problems = profiles.check_volume(profile, vol.file_system, parts, vol.scheme)
    desc = ", ".join(x for x in (vol.file_system, vol.scheme,
                                 "one partition" if parts == 1 else _plural(parts, "partition"))
                     if x)
    details = [p.text for p in problems]
    if parts > 1 and vol.scheme == disks.GPT:
        details.append("A GUID stick formatted on a Mac starts with a small EFI partition, "
                       "so “the first partition” a player reads is the empty one.")
    level = FAIL if any(p.level == FAIL for p in problems) else WARN if problems else PASS
    return Check("Stick", level, desc, details)


def _worst(levels):
    return FAIL if FAIL in levels else WARN if WARN in levels else PASS


LIBRARY_FILES = {profiles.DEVICE_LIBRARY: os.path.basename(pdb.RELATIVE),
                 profiles.ONE_LIBRARY: "exportLibrary.db"}
WHO_READS = {profiles.DEVICE_LIBRARY: "every player except the newest (CDJ-3000X, "
                                      "OPUS-QUAD, OMNIS-DUO, XDJ-AZ)",
             profiles.ONE_LIBRARY: "the newest players (CDJ-3000X, OPUS-QUAD, OMNIS-DUO, "
                                   "XDJ-AZ)"}


def check_export(stick, entries, profile=None):
    """(Check, set of rel paths the export lists).

    A rekordbox export holds one or both libraries (see profiles.DEVICE_LIBRARY):
    the stick needs whichever the chosen players read. Each one that's there is
    read, and every track it lists must be on the stick. OneLibrary is
    encrypted; it's read when Sortero has SQLCipher (onelibrary.available()),
    and otherwise only its being there is checked.
    """
    profile = profile or profiles.EVERY
    folder = os.path.join(stick, "PIONEER", "rekordbox")
    present = {lib for lib, fn in LIBRARY_FILES.items()
               if os.path.isfile(os.path.join(folder, fn))}
    if not present:
        if os.path.isdir(os.path.join(stick, "PIONEER")):
            return Check("rekordbox", FAIL, "there's a PIONEER folder but no rekordbox "
                         "library in it. Write the main stick again."), set()
        who = "players" if profile.brand == "Pioneer" else "Pioneer players"
        return Check("rekordbox", PASS, f"no rekordbox export, so {who} will browse "
                                        "it as folders."), set()

    levels, parts, details, listed = [], [], [], set()
    for lib in sorted((profile.libraries & profiles.REKORDBOX_LIBRARIES) - present):
        levels.append(FAIL)
        details.append(f"No {lib} ({LIBRARY_FILES[lib]}), which {WHO_READS[lib]} need to "
                       "list these playlists. Write the main stick again: Sortero writes "
                       "both (so does rekordbox 7.2.11 or later).")
    sizes = {name_key(e.rel): e.size for e in entries}

    def on_stick(tracks):
        """(missing, changed) for [(path on the stick, title, size)]."""
        missing, changed = [], []
        for path, title, size in tracks:
            rel = path.lstrip("/")
            listed.add(name_key(rel))
            if name_key(rel) not in sizes:
                missing.append(f"missing: {rel}" + (f" ({title})" if title else ""))
            elif size and sizes[name_key(rel)] != size:
                changed.append(f"changed since export: {rel}")
        return missing, changed

    one = None                  # OneLibrary's (tracks, playlists), when it can be read
    if profiles.ONE_LIBRARY in present and onelibrary.available():
        try:
            one = onelibrary.read(os.path.join(folder, LIBRARY_FILES[profiles.ONE_LIBRARY]))
        except onelibrary.OneLibraryError as e:
            return Check("rekordbox", FAIL, f"exportLibrary.db (OneLibrary) {e}. Write "
                                            "the main stick again.", details), set()
    if profiles.DEVICE_LIBRARY in present:
        try:
            export = pdb.read(os.path.join(stick, pdb.RELATIVE))
        except pdb.ExportError as e:
            return Check("rekordbox", FAIL, f"export.pdb can't be read: {e}. Write the "
                                            "main stick again.", details), set()
        missing, changed = on_stick((t.path, t.title, t.size) for t in export.tracks)
        if one is not None:
            m2, c2 = on_stick((p, t, z) for _, p, t, z in one[0])
            missing += [m for m in m2 if m not in missing]
            changed += [c for c in c2 if c not in changed]
            if sorted(t.path for t in export.tracks) != sorted(p for _, p, _, _ in one[0]):
                levels.append(WARN)
                parts.append("its two libraries list different tracks, so the newest "
                             "players won't show the same set as the others. Write "
                             "the main stick again")
        n, lists = len(export.tracks), len(export.playlists)
        if missing:
            levels.append(FAIL)
            parts.append(f"{len(missing)} of {_plural(n, 'track')} in the export aren't on "
                         "the stick. Write the main stick again, don't copy files by hand")
        elif changed:
            levels.append(WARN)
            parts.append(f"{_plural(n, 'track')}, all on the stick, but {len(changed)} "
                         "changed after the library was written, so their waveforms and "
                         "cues may be off")
        else:
            parts.append(f"{_plural(n, 'track')}"
                         + (f" in {_plural(lists, 'playlist')}" if lists else "")
                         + ", all on the stick")
        details = missing + changed + details
    elif one is not None:
        missing, changed = on_stick((p, t, z) for _, p, t, z in one[0])
        n = len(one[0])
        if missing:
            levels.append(FAIL)
            parts.append(f"{len(missing)} of {_plural(n, 'track')} in its OneLibrary "
                         "aren't on the stick. Write the main stick again, don't copy files by hand")
        else:
            parts.append(f"{_plural(n, 'track')} in OneLibrary, all on the stick")
        details = missing + changed + details
    elif profiles.ONE_LIBRARY in present:
        levels.append(WARN)
        parts.append("only a OneLibrary export, which is encrypted, and this copy of "
                     "Sortero can't open it, so it can't check its tracks are all there")
    if present == {profiles.DEVICE_LIBRARY, profiles.ONE_LIBRARY}:
        parts.append("both Device Library and OneLibrary are there")
    elif not parts:
        parts.append(f"{' and '.join(sorted(present))} only")
    summary = "; ".join(parts) + "."
    return Check("rekordbox", _worst(levels), summary[:1].upper() + summary[1:],
                 details), listed


def _bare(name):
    """A playlist name without the set number Gig prep puts in front ("01 Warmup")."""
    return re.sub(r"^\d+\s+", "", name).strip().casefold()


def check_main(stick, profile, playlists=None):
    """The main stick needs a library the chosen players read, and tonight's set in it.

    Either library will do when both makers' players are chosen; the one that's
    missing is a warning (those players browse folders instead). `playlists`
    are the names in Gig prep's XML; they're looked for in the Device Library,
    the one library Sortero can open.
    """
    pioneer = os.path.join(stick, "PIONEER", "rekordbox")
    has_rb = any(os.path.isfile(os.path.join(pioneer, fn)) for fn in LIBRARY_FILES.values())
    has_engine = os.path.isdir(os.path.join(stick, ENGINE_DIR))
    wanted = [(profiles.reads_rekordbox(profile), has_rb, "rekordbox", "Pioneer"),
              (profiles.reads_engine(profile), has_engine, "Engine DJ", "Denon")]
    wanted = [w for w in wanted if w[0]]
    apps = " or ".join(w[2] for w in wanted)
    if not any(w[1] for w in wanted):
        return Check("Main stick", FAIL, f"there's no {apps} library on it yet. Put the "
                     f"set on it with {apps}, then check it again.")
    levels, parts, details = [], [], []
    for _, has, app, brand in wanted:
        if not has:
            levels.append(WARN)
            parts.append(f"no {app} library, so {brand} players will only browse it as "
                         "folders")
    one_path = os.path.join(pioneer, LIBRARY_FILES[profiles.ONE_LIBRARY])
    on_stick = None                     # if neither can be read, the rekordbox check says why
    if has_rb and playlists and os.path.isfile(os.path.join(stick, pdb.RELATIVE)):
        try:
            on_stick = {_bare(n) for n in pdb.read(os.path.join(stick, pdb.RELATIVE)).playlists}
        except pdb.ExportError:
            pass
    elif has_rb and playlists and os.path.isfile(one_path) and onelibrary.available():
        try:
            on_stick = {_bare(n) for n in onelibrary.read(one_path)[1]}
        except onelibrary.OneLibraryError:
            pass
    if on_stick is not None:
        missing = [n for n in playlists if _bare(n) not in on_stick]
        if len(missing) == len(playlists):
            levels.append(FAIL)
            parts.append("none of tonight's playlists are in its rekordbox library. "
                         "Is it an older export? Put the set on it again")
        elif missing:
            levels.append(WARN)
            parts.append(f"{_plural(len(missing), 'playlist')} of tonight's set "
                         "isn't in its rekordbox library")
            details += [f"not in the library: {n}" for n in missing]
        else:
            parts.append(f"tonight's {_plural(len(playlists), 'playlist')} are in "
                         "its rekordbox library")
    if not parts:
        found = [w[2] for w in wanted if w[1]]
        parts.append(f"its {' and '.join(found)} "
                     f"{'library is' if len(found) == 1 else 'libraries are'} there")
    summary = "; ".join(parts) + "."
    return Check("Main stick", _worst(levels), summary, details)


def check_engine(stick, profile=None):
    """Is there an Engine database for Denon players? Either way the stick plays:
    without one they browse it as folders (Library > Folder)."""
    who = "players" if profile and profile.brand == "Denon" else "Denon players"
    if os.path.isdir(os.path.join(stick, ENGINE_DIR)):
        return Check("Engine DJ", PASS, f"an Engine library is there, so {who} list its "
                     "playlists. Sortero can't open it, so it checks the files on the "
                     "stick, not the playlists in it.")
    return Check("Engine DJ", PASS, f"no Engine library, so {who} will browse it as "
                                    "folders (Library > Folder).")


def _decode(path):
    """'' if the file decodes cleanly from start to finish, else the first error."""
    r = ffmpeg.run([ffmpeg.require("ffmpeg"), "-v", "error", "-xerror", "-nostdin", "-i", path,
                    "-map", "0:a:0", "-f", "null", "-"])
    msg = r.stderr.decode("utf-8", "replace").strip()
    if r.returncode != 0 or msg:
        return msg.splitlines()[0] if msg else "ffmpeg stopped part way"
    return ""


def _inspect(e, profile, decode):
    """(problems from the profile, decode error or None) for one audio file."""
    try:
        info = probe(e.path)
    except ProbeError as ex:
        return [f"{e.rel}: can't be read ({ex})"], None, False
    problems = [f"{e.rel}: {p.text}" for p in profiles.check_audio(profile, info)]
    if headers.stray_id3(e.path):
        problems.append(f"{e.rel}: its tags sit in front of the audio, which stricter "
                        "players may refuse (an older Sortero did this; repair the "
                        "original and copy it again)")
    unknown = not profile.formats_known
    return problems, (_decode(e.path) if decode else None), unknown


def check_audio(stick, entries, profile, decode=True, progress=None, workers=None):
    audio = [e for e in entries if is_audio(e)]
    formats, broken, unknown = [], [], False
    workers = workers or max(1, min(4, os.cpu_count() or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_inspect, e, profile, decode): e for e in audio}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            e = futures[fut]
            problems, error, unk = fut.result()
            unknown = unknown or unk
            formats += problems
            if error:
                broken.append(f"{e.rel}: {error}")
            if progress:
                progress(i, len(audio))
    formats.sort()
    broken.sort()
    n = len(audio)
    if unknown:
        fmt = Check("Formats", WARN, f"the {profile.name}'s manual has no format table, so "
                    f"{_plural(n, 'file')} couldn't be checked against it. Check against "
                    f"“{profiles.EVERY.name}” instead to be sure.")
    elif formats:
        fmt = Check("Formats", FAIL, f"{len({f.split(': ')[0] for f in formats})} of "
                    f"{_plural(n, 'file')} won't play on {profiles.where(profile)}.", formats)
    elif not n:
        fmt = Check("Formats", WARN, "there's no audio on the stick.")
    else:
        fmt = Check("Formats", PASS, f"{_all(n, 'audio file')} play on "
                                     f"{profiles.where(profile)}.")
    if not decode:
        dec = Check("Decode test", WARN, "skipped. Run the full check before you leave.")
    elif broken:
        dec = Check("Decode test", FAIL, f"{len(broken)} of {_plural(n, 'file')} don't "
                    "play cleanly to the end. Copy them again from the originals.", broken)
    else:
        dec = Check("Decode test", PASS, f"{_all(n, 'file')} play start to finish.")
    return fmt, dec


def check_names(entries, profile):
    fails, warns = [], []
    depth = profile.folder_depth    # None: the maker states no limit
    per_folder, splits = {}, {}
    from .usbexport import MARKER
    ours = any(e.rel == MARKER.replace(os.sep, "/") for e in entries)
    for e in entries:
        if is_appledouble(e.rel):
            continue
        folder = os.path.dirname(e.rel)
        per_folder[folder] = per_folder.get(folder, 0) + 1
        if e.size > FAT32_MAX:
            fails.append(f"{e.rel}: over 4 GB, which FAT32 can't hold")
        if depth and is_audio(e) and e.rel.count("/") > depth:
            fails.append(f"{e.rel}: more than {depth} folders deep, so players won't play it")
        problems = name_problems(e.rel)
        split = slash_split(e.rel)
        if split:
            # One line per tag, saying what to change, not one per file it split.
            where, tag, value = split
            fixed = value.replace("/", "-")
            splits[where] = (
                f"{where}/: the {tag} “{value}” has a “/”, which an earlier Sortero made "
                f"into folders. Write the main stick again: it's “{fixed}” now" if ours else
                f"{where}/: the {tag} “{value}” has a “/”, which rekordbox makes into "
                f"folders. Change the {tag} to “{fixed}” in rekordbox, then export again")
            problems = [p for p in problems if "ends in a space" not in p]
        fails += problems
    fails += splits.values()
    for folder, n in per_folder.items():
        if n > FOLDER_DISPLAY_MAX and profiles.reads_rekordbox(profile):
            warns.append(f"{folder or 'top level'}: {n:,} files; players show the first "
                         f"{FOLDER_DISPLAY_MAX:,}")
    dots = [e.rel for e in entries if is_appledouble(e.rel)]
    if dots:
        warns.append(f"{_plural(len(dots), 'macOS “._” file')} (e.g. {dots[0]}). Players "
                     "browsing folders may list them as tracks that won't load. Sortero's "
                     "backup step removes them.")
    if fails:
        return Check("Names and sizes", FAIL, f"{_plural(len(fails), 'problem')}.",
                     sorted(fails) + warns)
    if warns:
        return Check("Names and sizes", WARN, f"{_plural(len(warns), 'thing')} worth a look.",
                     warns)
    deep = f"nothing deeper than {depth} folders, " if depth else ""
    return Check("Names and sizes", PASS, f"nothing over 4 GB, {deep}every name "
                                          "FAT32-safe.")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_checksums(entries, sources, progress=None):
    """Compare stick files with the originals they were copied from.

    `sources` maps a stick path (relative, "/"-separated) to its original, or is
    a list of originals matched to stick files by name and size.
    """
    if not isinstance(sources, dict):
        by_key = {(os.path.basename(p).lower(), os.path.getsize(p)): p
                  for p in sources if os.path.isfile(p)}
        sources = {e.rel: by_key[(os.path.basename(e.rel).lower(), e.size)]
                   for e in entries if is_audio(e)
                   and (os.path.basename(e.rel).lower(), e.size) in by_key}
    on_stick = {e.rel: e for e in entries}
    bad, same = [], 0
    pairs = [(rel, src) for rel, src in sources.items() if rel in on_stick]
    for i, (rel, src) in enumerate(pairs, 1):
        if not os.path.isfile(src) or _sha(on_stick[rel].path) != _sha(src):
            bad.append(f"{rel}: differs from {src}")
        else:
            same += 1
        if progress:
            progress(i, len(pairs))
    if bad:
        return Check("Checksums", FAIL, f"{len(bad)} of {_plural(len(pairs), 'file')} "
                     "don't match their originals. Copy them again.", bad)
    if not pairs:
        return Check("Checksums", WARN, "no file on the stick could be matched to an "
                                        "original.")
    return Check("Checksums", PASS, f"{_plural(same, 'file')} match their originals "
                                    "byte for byte.")


# --------------------------------------------------------------------- run
def verify(stick, profile=None, decode=True, sources=None, vol=None, progress=None,
           role=None, playlists=None):
    """Run every check on the stick mounted at `stick`. Returns a Report.

    role=MAIN also checks there's a library for the players, with `playlists`
    (the names Gig prep gave them) in it."""
    profile = profile or profiles.EVERY
    report = Report(stick, profile)
    report.checks.append(check_stick(profile, vol or disks.volume(stick)))
    if role == MAIN:
        report.checks.append(check_main(stick, profile, playlists))
    entries = walk(stick)
    has_pioneer = os.path.isdir(os.path.join(stick, "PIONEER"))
    has_engine = os.path.isdir(os.path.join(stick, ENGINE_DIR))
    # On the main stick a missing library is the Main stick check's to report
    if has_pioneer or (profiles.reads_rekordbox(profile) and role != MAIN):
        exp, _ = check_export(stick, entries, profile)
        report.checks.append(exp)
    if profiles.reads_engine(profile) and (has_engine or role != MAIN):
        report.checks.append(check_engine(stick, profile))
    report.checks += check_audio(stick, entries, profile, decode, progress)
    report.audio = sum(1 for e in entries if is_audio(e))
    if any(os.path.isfile(os.path.join(stick, "PIONEER", "rekordbox", fn))
           for fn in LIBRARY_FILES.values()):
        report.libraries.append("rekordbox")
    if os.path.isdir(os.path.join(stick, ENGINE_DIR)):
        report.libraries.append("Engine DJ")
    report.checks.append(check_names(entries, profile))
    from . import backup
    # A stick Sortero wrote knows exactly where each file came from, which beats
    # matching by name and size.
    sources = backup.sources(stick) or sources
    if sources:
        report.checks.append(check_checksums(entries, sources))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m sortero.gig.verify",
                                 description="Check a USB stick before a gig.")
    ap.add_argument("stick", help="where the stick is mounted, e.g. /Volumes/GIG")
    ap.add_argument("--player", default=profiles.DEFAULT,
                    help="check for one player, e.g. cdj-2000 or sc6000 "
                         "(default: any Pioneer or Denon player)")
    ap.add_argument("--quick", action="store_true", help="skip the decode test")
    ap.add_argument("--originals", nargs="*", help="folders of originals to checksum against")
    args = ap.parse_args(argv)
    if args.player not in profiles.PROFILES:
        ap.error("players: " + ", ".join(profiles.PROFILES))
    sources = None
    if args.originals:
        sources = [os.path.join(d, f) for root in args.originals
                   for d, _, files in os.walk(root) for f in files]
    total = {"n": 0}

    def progress(i, n):
        total["n"] = n
        print(f"\rChecking audio… {i:,} of {n:,}", end="", file=sys.stderr, flush=True)

    report = verify(args.stick, profiles.get(args.player), not args.quick, sources,
                    progress=progress)
    if total["n"]:
        print(file=sys.stderr)
    print("\n".join(report.lines()))
    return 0 if report.ready else 1


if __name__ == "__main__":
    sys.exit(main())
