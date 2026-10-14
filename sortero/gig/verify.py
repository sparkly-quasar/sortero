"""Check a mounted stick before a gig, and say plainly whether it's ready.

Six checks, each pass, warn or fail:

  Stick            file system, partition map, partitions (profiles.check_volume)
  rekordbox        export.pdb is there and readable, and every track it lists
                   is on the stick, the size rekordbox exported
  Formats          every audio file plays on the chosen players
  Decode test      every audio file decodes from start to finish
  Names and sizes  nothing a FAT32 stick or a player can't handle
  Checksums        optional: files match the originals they came from

Any fail means NOT READY. Warnings are listed but don't stop the gig.

Also runs from a terminal:

    python -m sortero.gig.verify /Volumes/GIG [--player cdj-2000] [--quick]
"""
import argparse, concurrent.futures, hashlib, os, re, sys
from dataclasses import dataclass, field

from .. import headers
from . import disks, ffmpeg, pdb, profiles
from .probe import ProbeError, probe

PASS, WARN, FAIL = "pass", profiles.WARN, profiles.FAIL
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".mp4", ".wav", ".aif", ".aiff", ".flac", ".fla",
              ".ogg", ".opus", ".wma", ".alac"}
# Folders operating systems keep on a stick for themselves.
SYSTEM_DIRS = {".Spotlight-V100", ".Trashes", ".fseventsd", ".TemporaryItems",
               "System Volume Information", "$RECYCLE.BIN", ".DocumentRevisions-V100"}
FAT32_MAX = 4 * 1024 ** 3 - 1           # largest file FAT32 can hold
FOLDER_DISPLAY_MAX = 10000              # players show no more files than this per folder
FAT_ILLEGAL = re.compile(r'[\x00-\x1f"*:<>?\\|]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}
SHOWN = 4                               # detail lines per check, so it fits on one screen


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

    def lines(self):
        out = [f"Checking {self.stick} for {profiles.where(self.profile)}", ""]
        for c in self.checks:
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


def check_export(stick, entries):
    """(Check, set of rel paths the export lists)."""
    path = os.path.join(stick, pdb.RELATIVE)
    has_pioneer = os.path.isdir(os.path.join(stick, "PIONEER"))
    if not os.path.isfile(path):
        if has_pioneer:
            return Check("rekordbox", FAIL, "there's a PIONEER folder but no "
                         "rekordbox/export.pdb in it. Export the stick again from "
                         "rekordbox."), set()
        return Check("rekordbox", PASS, "no rekordbox export, so players will browse "
                                        "it as folders."), set()
    try:
        export = pdb.read(path)
    except pdb.ExportError as e:
        return Check("rekordbox", FAIL, f"export.pdb can't be read: {e}. Export the stick "
                                        "again from rekordbox."), set()
    sizes = {e.rel.lower(): e.size for e in entries}
    missing, changed, listed = [], [], set()
    for t in export.tracks:
        rel = t.path.lstrip("/")
        listed.add(rel.lower())
        if rel.lower() not in sizes:
            missing.append(f"missing: {rel}" + (f" ({t.title})" if t.title else ""))
        elif t.size and sizes[rel.lower()] != t.size:
            changed.append(f"changed since export: {rel}")
    n = len(export.tracks)
    extra = [x for x in pdb.EXTRA if os.path.isfile(os.path.join(stick, "PIONEER", "rekordbox", x))]
    tail = f" (plus {', '.join(extra)} for newer players)" if extra else ""
    if missing:
        return Check("rekordbox", FAIL, f"{len(missing)} of {_plural(n, 'track')} in the "
                     f"export aren't on the stick{tail}. Export again from rekordbox, "
                     "don't copy files by hand.", missing + changed), listed
    if changed:
        return Check("rekordbox", WARN, f"{_plural(n, 'track')}, all on the stick, but "
                     f"{len(changed)} changed after rekordbox exported them{tail}. "
                     "Their waveforms and cues may be off.", changed), listed
    lists = len(export.playlists)
    return Check("rekordbox", PASS, f"{_plural(n, 'track')}"
                 + (f" in {_plural(lists, 'playlist')}" if lists else "")
                 + f", all on the stick{tail}."), listed


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
                    "“Any Pioneer player” instead to be sure.")
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
    depth = profile.folder_depth or profiles.UNIVERSAL.folder_depth
    per_folder = {}
    for e in entries:
        if is_appledouble(e.rel):
            continue
        folder = os.path.dirname(e.rel)
        per_folder[folder] = per_folder.get(folder, 0) + 1
        if e.size > FAT32_MAX:
            fails.append(f"{e.rel}: over 4 GB, which FAT32 can't hold")
        if is_audio(e) and e.rel.count("/") > depth:
            fails.append(f"{e.rel}: more than {depth} folders deep, so players won't play it")
        fails += name_problems(e.rel)
    for folder, n in per_folder.items():
        if n > FOLDER_DISPLAY_MAX:
            warns.append(f"{folder or 'top level'}: {n:,} files; players show the first "
                         f"{FOLDER_DISPLAY_MAX:,}")
    dots = [e.rel for e in entries if is_appledouble(e.rel)]
    if dots:
        warns.append(f"{_plural(len(dots), 'macOS “._” file')} (e.g. {dots[0]}). Players "
                     "browsing folders may list them as tracks that won't load. Sortero's "
                     "backup step removes them, or run dot_clean on the stick.")
    if fails:
        return Check("Names and sizes", FAIL, f"{_plural(len(fails), 'problem')}.",
                     sorted(fails) + warns)
    if warns:
        return Check("Names and sizes", WARN, f"{_plural(len(warns), 'thing')} worth a look.",
                     warns)
    return Check("Names and sizes", PASS, "nothing over 4 GB, nothing deeper than "
                                          f"{depth} folders, every name FAT32-safe.")


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
def verify(stick, profile=None, decode=True, sources=None, vol=None, progress=None):
    """Run every check on the stick mounted at `stick`. Returns a Report."""
    profile = profile or profiles.UNIVERSAL
    report = Report(stick, profile)
    report.checks.append(check_stick(profile, vol or disks.volume(stick)))
    entries = walk(stick)
    exp, _ = check_export(stick, entries)
    report.checks.append(exp)
    report.checks += check_audio(stick, entries, profile, decode, progress)
    report.checks.append(check_names(entries, profile))
    if sources:
        report.checks.append(check_checksums(entries, sources))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m sortero.gig.verify",
                                 description="Check a USB stick before a gig.")
    ap.add_argument("stick", help="where the stick is mounted, e.g. /Volumes/GIG")
    ap.add_argument("--player", default=profiles.DEFAULT,
                    help="check for one player, e.g. cdj-2000 (default: any Pioneer player)")
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
