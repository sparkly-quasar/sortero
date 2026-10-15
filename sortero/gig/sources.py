"""Where Gig prep's playlists come from: Sortero's own, or ones you already have.

Sortero's own playlists, the .m3u8 files in the collection's _Playlists folder,
are always listed. Others are added from Gig prep and remembered:

  * a playlist file (.m3u or .m3u8), as rekordbox, Traktor, Mixxx, VirtualDJ or
    Apple Music export them, wherever it lives
  * a watched folder of playlist files: every .m3u or .m3u8 in it, or in the
    folders inside it, is listed each time Gig prep looks, so a new export
    turns up without being added
  * a folder of tracks, played in filename order ("01 ...", "02 ...")
  * a playlist from a rekordbox XML export (File > Export Collection in xml
    format), found by its place in rekordbox's playlist folders
  * a Mixxx playlist or crate, read straight from Mixxx's library

Each is read again whenever it's used, so a playlist changed in the app that
made it is picked up. Nothing here ever writes to any of them.
"""
import os, re, sqlite3, urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .. import playlists, settings
from ..common import AUDIO_EXTS
from .cues import location_path

SORTERO, M3U, FOLDER, REKORDBOX, MIXXX = "sortero", "m3u", "folder", "rekordbox", "mixxx"
WATCHED = "watched"
PLAYLIST_EXTS = (".m3u", ".m3u8")
# rekordbox XML: playlist NODEs are Type 1 (Type 0 is a folder); a playlist's
# TRACK Keys are TrackIDs when KeyType is 0, Locations when it's 1.
RB_FOLDER, RB_PLAYLIST = "0", "1"
# Mixxx keeps Auto DJ's queue (1) and the history (2) as hidden playlists
# (PlaylistDAO::HiddenType in Mixxx's src/library/dao/playlistdao.h).
MIXXX_SHOWN = 0


class SourceError(Exception):
    pass


@dataclass(frozen=True)
class Source:
    kind: str
    path: str               # playlist file, track folder, XML, or Mixxx database
    name: str               # the playlist's own name
    ref: tuple = ()         # rekordbox: folder names, then the playlist's;
                            # Mixxx: ("playlist" or "crate", id);
                            # watched: (the watched folder,)

    @property
    def key(self):
        """Stable and unique, for remembering what's ticked. Sortero's own
        playlists keep their bare names, as Gig prep stored them before."""
        if self.kind == SORTERO:
            return self.name
        return "\x1f".join([self.kind, self.path, *map(str, self.ref)])

    @property
    def where(self):
        """Where it comes from, in a few words."""
        if self.kind == REKORDBOX:
            folders = " › ".join(self.ref[:-1])
            return f"rekordbox{': ' + folders if folders else ''}"
        if self.kind == MIXXX:
            return "Mixxx crate" if self.ref[0] == "crate" else "Mixxx playlist"
        if self.kind == SORTERO:
            return "Sortero"
        return self.path

    @property
    def folder(self):
        """The watched folder a watched playlist was found in."""
        return self.ref[0] if self.kind == WATCHED and self.ref else ""

    def to_dict(self):
        return {"kind": self.kind, "path": self.path, "name": self.name,
                "ref": list(self.ref)}

    @classmethod
    def from_dict(cls, d):
        return cls(d["kind"], d["path"], d["name"], tuple(d.get("ref") or ()))


# ------------------------------------------------------------- remembering
def added():
    """The playlists added from elsewhere, in the order they were added."""
    out = []
    for d in settings.get("gig_sources") or []:
        try:
            out.append(Source.from_dict(d))
        except (KeyError, TypeError):
            continue
    return out


def remember(new):
    """Add these to the remembered list, skipping any already there. Returns
    the ones that were new."""
    have = added()
    keys = {s.key for s in have}
    fresh = []
    for s in new:
        if s.key not in keys:
            keys.add(s.key)
            fresh.append(s)
    settings.set("gig_sources", [s.to_dict() for s in have + fresh])
    return fresh


def forget(key):
    settings.set("gig_sources", [s.to_dict() for s in added() if s.key != key])


def watched():
    """The folders of playlist files Gig prep watches, in the order added."""
    return [f for f in settings.get("gig_watch_folders") or [] if isinstance(f, str)]


def watch(folder):
    """Start watching a folder. Returns False if it was already watched."""
    folder = os.path.abspath(folder)
    have = watched()
    if folder in have:
        return False
    settings.set("gig_watch_folders", have + [folder])
    return True


def unwatch(folder):
    settings.set("gig_watch_folders", [f for f in watched() if f != folder])


def watched_sources(skip=()):
    """Every playlist file in the watched folders, found afresh. A folder that
    isn't there (a drive that's unplugged) contributes nothing. `skip` holds
    playlist files already listed some other way."""
    out, seen = [], set(skip)
    for folder in watched():
        if not os.path.isdir(folder):
            continue
        for p in find_m3u(folder):
            if p in seen:
                continue
            seen.add(p)
            out.append(Source(WATCHED, p, os.path.splitext(os.path.basename(p))[0],
                              (folder,)))
    return out


def missing_watched():
    return [f for f in watched() if not os.path.isdir(f)]


def listed(root):
    """Every playlist Gig prep offers: Sortero's own first, then the watched
    folders' playlists, then the added ones."""
    own = []
    if root and os.path.isdir(root):
        own = [Source(SORTERO, playlists.playlist_dir(root), n)
               for n in playlists.existing(root)]
    extra = added()
    # Sortero's own playlists, or a file added by hand, aren't listed twice
    # just because they sit in a watched folder too.
    skip = {s.path for s in extra if s.kind == M3U}
    if root:
        skip |= {os.path.join(playlists.playlist_dir(root), n + ".m3u8")
                 for n in playlists.existing(root)}
    return own + watched_sources(skip) + extra


# ------------------------------------------------------------------ tracks
def tracks(src, root=None):
    """The paths a playlist points at, in order, whether or not they exist.

    Raises SourceError when the playlist itself can't be read.
    """
    try:
        if src.kind == SORTERO:
            return playlists.entries(root, src.name)
        if src.kind in (M3U, WATCHED):
            return read_m3u(src.path)
        if src.kind == FOLDER:
            return folder_tracks(src.path)
        if src.kind == REKORDBOX:
            return _rekordbox(src.path).tracks(src.ref)
        if src.kind == MIXXX:
            return _mixxx_tracks(src.path, *src.ref)
    except (OSError, ET.ParseError, sqlite3.Error) as e:
        raise SourceError(_why(src, e)) from e
    raise SourceError(f"Sortero doesn't know how to read “{src.name}”.")


def _why(src, e):
    if isinstance(e, FileNotFoundError):
        return f"{src.path} isn't there any more."
    if isinstance(e, ET.ParseError):
        return f"{os.path.basename(src.path)} isn't a rekordbox XML Sortero can read ({e})."
    if isinstance(e, sqlite3.Error):
        return f"Mixxx's library couldn't be read ({e})."
    return f"{src.path} couldn't be read ({e.strerror or e})."


def resolve(chosen, root, log=print):
    """[(name, [paths])] for these Sources, in order. One that can't be read
    is logged and left out, so the rest of the gig still goes ahead."""
    sets = []
    for src in chosen:
        try:
            sets.append((src.name, tracks(src, root)))
        except SourceError as e:
            log(f"Couldn't read the playlist “{src.name}”: {e}")
    return sets


# -------------------------------------------------------------------- m3u
def _decode(raw, ext):
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # .m3u from older apps (iTunes, Winamp) is in the system's code page;
        # on the machines DJs use, that's nearly always Windows-1252.
        return raw.decode("utf-8" if ext == ".m3u8" else "cp1252", "replace")


_WINDOWS_ABS = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")


def _entry_path(line, base):
    if line.lower().startswith("file:"):
        return location_path(line)
    if re.match(r"^[a-z][a-z0-9+.-]*://", line, re.I):
        return None                                 # a stream URL, not a file
    if os.sep == "/" and "\\" in line:
        if _WINDOWS_ABS.match(line):
            return line                             # C:\... on a Mac: can't be here
        line = line.replace("\\", "/")
    p = os.path.expanduser(line)
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(base, p))


def read_m3u(path):
    """The tracks an .m3u or .m3u8 lists, in order. Relative paths are taken
    from the playlist's own folder; #EXTINF and other comments are skipped."""
    with open(path, "rb") as fh:
        text = _decode(fh.read(), os.path.splitext(path)[1].lower())
    base = os.path.dirname(os.path.abspath(path))
    out = []
    for line in text.splitlines():     # also splits Apple Music's bare \r endings
        s = line.strip()
        if s and not s.startswith("#"):
            p = _entry_path(s, base)
            if p:
                out.append(p)
    return out


def m3u_sources(paths):
    return [Source(M3U, os.path.abspath(p), os.path.splitext(os.path.basename(p))[0])
            for p in paths]


def find_m3u(folder):
    """Every playlist file in this folder and the folders inside it, by name."""
    found = []
    for dp, dns, fns in os.walk(folder):
        dns[:] = sorted(d for d in dns if not d.startswith("."))
        found += [os.path.join(dp, f) for f in fns
                  if f.lower().endswith(PLAYLIST_EXTS) and not f.startswith("._")]
    return sorted(found, key=lambda p: natural(os.path.relpath(p, folder)))


# ----------------------------------------------------------------- folders
def natural(s):
    """Sort key that puts "2 Intro" before "10 Outro"."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.casefold())
            for t in re.split(r"(\d+)", s) if t]


def folder_tracks(folder):
    """The audio in a folder and the folders inside it, in filename order."""
    if not os.path.isdir(folder):
        raise FileNotFoundError(folder)
    found = []
    for dp, dns, fns in os.walk(folder):
        dns[:] = [d for d in dns if not d.startswith(".")]
        found += [os.path.join(dp, f) for f in fns
                  if os.path.splitext(f)[1].lower() in AUDIO_EXTS and not f.startswith(".")]
    return sorted(found, key=lambda p: natural(os.path.relpath(p, folder)))


def folder_source(folder):
    folder = os.path.abspath(folder)
    return Source(FOLDER, folder, os.path.basename(os.path.normpath(folder)) or folder)


# --------------------------------------------------------------- rekordbox
class _RekordboxXML:
    def __init__(self, path):
        self.path = path
        locations, self.playlists = {}, []         # [(ref, keytype, [keys])]
        stack = []
        for event, el in ET.iterparse(path, events=("start", "end")):
            if el.tag == "NODE":
                if event == "start":
                    stack.append(el.get("Name") or "")
                    continue
                kind, ref = el.get("Type"), tuple(stack[1:])   # [0] is ROOT
                stack.pop()
                if kind == RB_PLAYLIST:
                    keys = [t.get("Key") for t in el.findall("TRACK") if t.get("Key")]
                    self.playlists.append((ref, el.get("KeyType") or "0", keys))
                el.clear()
            elif event == "end" and el.tag == "TRACK" and el.get("Location"):
                if el.get("TrackID"):
                    locations[el.get("TrackID")] = el.get("Location")
                el.clear()
        self.locations = locations

    def sources(self):
        return [Source(REKORDBOX, self.path, ref[-1] if ref else "Untitled", ref)
                for ref, _, _ in self.playlists]

    def tracks(self, ref):
        for r, keytype, keys in self.playlists:
            if r == tuple(ref):
                locs = keys if keytype == "1" else [self.locations.get(k) for k in keys]
                return [location_path(l) for l in locs if l]
        raise SourceError(f"There's no playlist “{' › '.join(ref)}” in "
                          f"{os.path.basename(self.path)} any more.")


_xml_cache = {}


def _rekordbox(path):
    stamp = os.stat(path).st_mtime_ns
    hit = _xml_cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    xml = _RekordboxXML(path)
    _xml_cache.clear()          # one big XML at a time is plenty to hold on to
    _xml_cache[path] = (stamp, xml)
    return xml


def rekordbox_sources(xml_path):
    """Every playlist in a rekordbox XML, in rekordbox's own order."""
    try:
        return _rekordbox(xml_path).sources()
    except (OSError, ET.ParseError) as e:
        raise SourceError(_why(Source(REKORDBOX, xml_path, ""), e)) from e


# ------------------------------------------------------------------- Mixxx
def _connect(db):
    if not os.path.isfile(db):
        raise FileNotFoundError(db)
    # read-only, so Mixxx can stay open
    return sqlite3.connect(f"file:{urllib.parse.quote(db)}?mode=ro", uri=True)


def mixxx_sources(db):
    """Mixxx's playlists in its own order, then its crates by name."""
    try:
        con = _connect(db)
        try:
            pls = con.execute("SELECT id, name FROM Playlists WHERE hidden = ? "
                              "ORDER BY position, id", (MIXXX_SHOWN,)).fetchall()
            crates = con.execute("SELECT id, name FROM crates "
                                 "ORDER BY name COLLATE NOCASE").fetchall()
        finally:
            con.close()
    except (OSError, sqlite3.Error) as e:
        raise SourceError(_why(Source(MIXXX, db, ""), e)) from e
    return ([Source(MIXXX, db, n or "Untitled", ("playlist", i)) for i, n in pls] +
            [Source(MIXXX, db, n or "Untitled", ("crate", i)) for i, n in crates])


def _mixxx_tracks(db, what, ident):
    con = _connect(db)
    try:
        if what == "playlist":
            if not con.execute("SELECT 1 FROM Playlists WHERE id = ?", (ident,)).fetchone():
                raise SourceError("That Mixxx playlist has been deleted.")
            rows = con.execute(
                "SELECT tl.location FROM PlaylistTracks pt "
                "JOIN library l ON l.id = pt.track_id "
                "JOIN track_locations tl ON tl.id = l.location "
                "WHERE pt.playlist_id = ? ORDER BY pt.position", (ident,)).fetchall()
        else:
            if not con.execute("SELECT 1 FROM crates WHERE id = ?", (ident,)).fetchone():
                raise SourceError("That Mixxx crate has been deleted.")
            # a crate has no order of its own, so it plays in filename order
            rows = con.execute(
                "SELECT tl.location FROM crate_tracks ct "
                "JOIN library l ON l.id = ct.track_id "
                "JOIN track_locations tl ON tl.id = l.location "
                "WHERE ct.crate_id = ?", (ident,)).fetchall()
            rows.sort(key=lambda r: natural(os.path.basename(r[0] or "")))
    finally:
        con.close()
    return [os.path.normpath(r[0]) for r in rows if r[0]]
