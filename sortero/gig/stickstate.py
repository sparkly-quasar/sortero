"""What's on a stick before Gig prep writes to it, and what writing will do.

Gig prep used to find out only once you'd pressed Write: a stick the players
can't read, or one with a library Sortero didn't write, stopped the job with no
way forward but another stick. read() looks first, so the step can say plainly:

  empty, or needs formatting
  a library Sortero wrote (when, its playlists): writing replaces them
  a rekordbox or Engine DJ library it didn't write: it can be replaced, which
      deletes that library and the music it put on the stick, and nothing else
  Sortero's folders backup, and anything else, which writing leaves alone

replace() does the replacing, so a stick that's been someone's rekordbox export
can become tonight's without formatting it.
"""
import json, os, shutil, time, unicodedata
from dataclasses import dataclass, field

from . import backup, disks, enginelib, pdb, usbexport, verify

REKORDBOX, ENGINE = "rekordbox", "Engine DJ"
SHOWN_NAMES = 4                 # playlist names spelled out before "and N more"


@dataclass
class Library:
    app: str                    # REKORDBOX or ENGINE
    ours: bool                  # Sortero wrote it and nothing has changed it since
    why: str = ""               # why it isn't Sortero's, when it isn't
    playlists: list = field(default_factory=list)
    tracks: int = 0
    audio: list = field(default_factory=list)   # stick paths ("a/b.mp3") of its music on the stick
    readable: bool = True

    def names(self):
        """Its playlists, a few by name."""
        ps = self.playlists
        if not ps:
            return "no playlists"
        shown = ", ".join(f"“{p}”" for p in ps[:SHOWN_NAMES])
        more = f" and {len(ps) - SHOWN_NAMES} more" if len(ps) > SHOWN_NAMES else ""
        return f"{_n(len(ps), 'playlist')} ({shown}{more})"


@dataclass
class State:
    stick: str
    name: str
    check: verify.Check                         # the file system, as the players see it
    libraries: list = field(default_factory=list)
    written: str = ""                           # when Sortero last wrote its library
    backup_when: str = ""                       # Sortero's folders backup, if any
    backup_files: int = 0
    backup_folders: list = field(default_factory=list)
    other_files: int = 0                        # nobody's library, backup or system's
    other_bytes: int = 0
    free: int = 0
    total: int = 0

    @property
    def needs_format(self):
        return self.check.level == verify.FAIL

    @property
    def empty(self):
        return not (self.libraries or self.backup_files or self.other_files)

    def foreign(self, engine=True):
        """Libraries Sortero didn't write that are in the way of writing the main
        stick (an Engine DJ one only when it's writing Engine's too)."""
        return [lib for lib in self.libraries
                if not lib.ours and (engine or lib.app != ENGINE)]

    # ----------------------------------------------------------- in words
    def lines(self):
        """What's on the stick, a sentence each."""
        from ..common import human_size
        out = []
        if self.needs_format:
            out.append(f"The players can't read it: {self.check.summary}")
        if self.empty:
            out.append("It's empty.")
        ours = [lib for lib in self.libraries if lib.ours]
        if ours:
            apps = " and ".join(lib.app for lib in ours)
            lib = ours[0]
            when = f" on {self.written}" if self.written else ""
            out.append(f"Sortero wrote a set here{when} for {apps}: {lib.names()}, "
                       f"{_n(lib.tracks, 'track')}.")
        for lib in self.libraries:
            if lib.ours:
                continue
            if lib.readable:
                out.append(f"A {lib.app} library Sortero didn't write: {lib.names()}, "
                           f"{_n(lib.tracks, 'track')}.")
            else:
                out.append(f"A {lib.app} library Sortero didn't write, which it can't "
                           "read.")
        if self.backup_files:
            when = f" from {self.backup_when}" if self.backup_when else ""
            out.append(f"Sortero's folders backup{when}: "
                       f"{_n(len(self.backup_folders), 'folder')}, "
                       f"{_n(self.backup_files, 'track')}.")
        if self.other_files:
            out.append(f"{_n(self.other_files, 'other file')} "
                       f"({human_size(self.other_bytes)}).")
        if self.total:
            out.append(f"{human_size(self.free)} free of {human_size(self.total)}.")
        return out

    def main_plan(self, engine=True):
        """What writing the main stick will do to what's here."""
        if self.needs_format:
            return "Format it first: everything on it is erased."
        theirs = self.foreign(engine)
        if theirs:
            what = " and ".join(f"the {lib.app} library" for lib in theirs)
            return (f"To write tonight's set here, Sortero replaces {what}: "
                    f"{replace_summary(theirs)} Or export from {theirs[0].app} yourself, "
                    "which adds to it.")
        keep = []
        if self.backup_files:
            keep.append("the folders backup")
        if self.other_files:
            keep.append(_n(self.other_files, "other file"))
        one = len(keep) == 1 and (self.backup_files or self.other_files == 1)
        stays = (f" {_join(keep)[:1].upper() + _join(keep)[1:]} "
                 f"{'stays' if one else 'stay'}.") if keep else ""
        if any(lib.ours for lib in self.libraries):
            return ("Writing replaces Sortero's playlists with tonight's, and removes the "
                    f"tracks it put here that aren't in tonight's set.{stays}")
        return f"Writing adds tonight's set.{stays}"

    def backup_plan(self):
        """What writing the folders backup will do to what's here."""
        if self.needs_format:
            return "Format it first: everything on it is erased."
        if self.backup_files:
            return ("Writing brings Sortero's folders up to date with tonight's set. "
                    "Nothing else on the stick is touched.")
        return "Writing adds tonight's set as folders. Nothing else on the stick is touched."


def _n(n, one):
    return f"{n:,} {one if n == 1 else one + 's'}"


def _join(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def replace_summary(libs):
    """What replacing these libraries deletes, and what it keeps."""
    # Both libraries on one stick usually list the same music and playlists
    music = len({a for lib in libs for a in lib.audio})
    lists = len({p for lib in libs for p in lib.playlists})
    gone = [_n(lists, "playlist")] if lists else []
    if music:
        gone.append(f"the {_n(music, 'music file')} it put on the stick")
    gone = _join(gone) if gone else "its database"
    return f"that deletes {gone}. Everything else on the stick stays."


# ------------------------------------------------------------------ reading
def _rekordbox(stick, entries):
    path = os.path.join(stick, pdb.RELATIVE)
    extra = [os.path.join(os.path.dirname(pdb.RELATIVE), x) for x in pdb.EXTRA]
    if not any(os.path.exists(os.path.join(stick, r)) for r in [pdb.RELATIVE] + extra):
        return None
    why = usbexport.foreign(stick, engine=False)
    lib = Library(REKORDBOX, ours=not why, why=why or "")
    try:
        ex = pdb.read(path)
    except (pdb.ExportError, OSError):
        lib.readable = False
        return lib
    lib.playlists = list(ex.playlists)
    lib.tracks = len(ex.tracks)
    lib.audio = _on_stick((t.path for t in ex.tracks), entries)
    return lib


def _engine(stick, entries):
    path = os.path.join(stick, enginelib.DATABASE)
    old = os.path.isfile(os.path.join(stick, enginelib.OLD_DATABASE))
    if not (os.path.isfile(path) or old):
        return None
    why = usbexport.engine_foreign(stick)
    lib = Library(ENGINE, ours=not why, why=why or "")
    if not os.path.isfile(path):
        lib.readable = False
        return lib
    try:
        tracks, lists = enginelib.read(path)
    except enginelib.EngineError:
        lib.readable = False
        return lib
    lib.playlists = [p for p in lists if p != "Gig"] if lib.ours else list(lists)
    lib.tracks = len(tracks)
    lib.audio = _on_stick((t[1] for t in tracks), entries)
    return lib


def _key(rel):
    """A stick path as it compares: macOS and Windows store accents differently."""
    return unicodedata.normalize("NFC", rel.strip("/"))


def _on_stick(paths, entries):
    """The stick paths, as on the stick, of those of `paths` that are there.
    `entries` maps _key(path) to the path as the stick has it."""
    return [entries[_key(p)] for p in paths if p and _key(p) in entries]


def _when(stamp):
    """"2026-11-20 12:58" as "20 Nov at 12:58"."""
    try:
        t = time.strptime(stamp, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return stamp or ""
    return f"{t.tm_mday} {time.strftime('%b', t)} at {time.strftime('%H:%M', t)}"


# Folders a library keeps for itself, besides its music
_LIBRARY_DIRS = ("PIONEER/", enginelib.FOLDER + "/")


def read(stick, profile=None, vol=None):
    """What's on the stick at `stick`."""
    name = os.path.basename(os.path.normpath(stick)) or stick
    check = verify.check_stick(profile or verify.profiles.EVERY, vol or disks.volume(stick))
    files = {e.rel: e for e in verify.walk(stick) if not verify.is_appledouble(e.rel)}
    keyed = {_key(rel): rel for rel in files}
    s = State(stick, name, check)
    for lib in (_rekordbox(stick, keyed), _engine(stick, keyed)):
        if lib:
            s.libraries.append(lib)
    mark = usbexport._marker(stick) or {}
    if any(lib.ours for lib in s.libraries):
        s.written = _when(mark.get("when", ""))
    man = backup.read_manifest(stick)
    on = _on_stick(man, keyed)
    s.backup_files = len(on)
    s.backup_folders = sorted({rel.split("/")[0] for rel in on})
    if on:
        try:
            with open(os.path.join(stick, backup.MANIFEST), encoding="utf-8") as fh:
                s.backup_when = _when(json.load(fh).get("when", ""))
        except (OSError, ValueError):
            pass
    known = (set(on) | {a for lib in s.libraries for a in lib.audio}
             | set(_on_stick(mark.get("audio", []), keyed)))
    for rel, e in files.items():
        if (rel in known or rel.startswith(_LIBRARY_DIRS) or rel == backup.MANIFEST
                or rel.startswith(".")):
            continue
        s.other_files += 1
        s.other_bytes += e.size
    try:
        du = shutil.disk_usage(stick)
        s.free, s.total = du.free, du.total
    except OSError:
        pass
    return s


# ---------------------------------------------------------------- replacing
def replace(state, engine=True, log=print):
    """Delete the libraries Sortero didn't write that are in the way, and the music
    they listed, so the main stick can be written. Everything else stays: Sortero's
    own files, its folders backup, and whatever else is on the stick."""
    stick = state.stick
    mine = set(backup.read_manifest(stick))         # the folders backup's: never these
    removed = 0
    for lib in state.foreign(engine):
        audio = list(lib.audio)
        if lib.app == REKORDBOX:
            # rekordbox exported over a set Sortero wrote: that set's music goes too
            keyed = {_key(e.rel): e.rel for e in verify.walk(stick)}
            audio += _on_stick((usbexport._marker(stick) or {}).get("audio", []), keyed)
        for rel in dict.fromkeys(audio):
            if rel in mine:
                continue
            try:
                os.remove(os.path.join(stick, *rel.split("/")))
                removed += 1
            except OSError:
                pass
        if lib.app == REKORDBOX:
            folder = os.path.join(stick, os.path.dirname(pdb.RELATIVE))
            for fn in [os.path.basename(pdb.RELATIVE), *pdb.EXTRA,
                       os.path.basename(usbexport.MARKER)]:
                try:
                    os.remove(os.path.join(folder, fn))
                except FileNotFoundError:
                    pass
            for sub in ("USBANLZ", "Artwork"):  # rekordbox's analysis and pictures
                shutil.rmtree(os.path.join(stick, "PIONEER", sub), ignore_errors=True)
        else:
            shutil.rmtree(os.path.join(stick, enginelib.FOLDER), ignore_errors=True)
        log(f"Removed the {lib.app} library Sortero didn't write.")
    _prune_empty(os.path.join(stick, usbexport.CONTENTS))
    if removed:
        log(f"Removed {_n(removed, 'music file')} it had put on the stick.")
    return removed


def _prune_empty(top):
    """Remove folders left empty under `top` (not `top` itself)."""
    for dirpath, dirnames, filenames in os.walk(top, topdown=False):
        if dirpath != top and not os.listdir(dirpath):
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
