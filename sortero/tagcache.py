"""Remember each file's tags between scans, so a rescan reads only what changed.

Every change Sortero makes is followed by a scan of the whole collection, and
reading tags is nearly all of a scan's time (about 5 s for 4,300 tracks, where
listing the files takes a fiftieth of a second). A file's tags are read again
only when the file itself says it changed:

  * its size or modification time differs, or
  * on macOS and Linux, its change time (ctime) does. That one can't be set by
    any app, so a tagger that writes tags but keeps the file's dates is still
    caught. (On Windows ctime is the creation time, so it isn't used there.)

A file Sortero moved keeps its entry: it's found again by its identity on disk
(device and inode) with the same size and modification time. A rename updates
ctime on some systems, which is why that match leaves ctime out.

The cache lives in the app-data folder and starts afresh with each new version
of Sortero, in case what a scan reads has changed. File → Re-read Every File's
Tags empties it.
"""
import json, os, sys, threading

from . import paths
from .version import __version__

FILE = "tag-cache.json"
_USE_CTIME = not sys.platform.startswith("win")
_lock = threading.RLock()
_entries = None          # {path: entry}, loaded on first use


def _file():
    return os.path.join(paths.data_dir(), FILE)


def _load():
    global _entries
    if _entries is None:
        try:
            with open(_file(), encoding="utf-8") as fh:
                data = json.load(fh)
            ok = isinstance(data, dict) and data.get("version") == __version__
            _entries = data.get("entries", {}) if ok else {}
        except (OSError, ValueError):
            _entries = {}
    return _entries


def stamp(st):
    """What a file's stat says about whether it changed."""
    return {"size": st.st_size, "mtime": st.st_mtime_ns,
            "ctime": st.st_ctime_ns if _USE_CTIME else 0,
            "dev": st.st_dev, "ino": st.st_ino}


def _same(entry, s, moved=False):
    keys = ("size", "mtime", "dev", "ino") if moved else ("size", "mtime", "ctime")
    return all(entry.get(k) == s[k] for k in keys)


class Session:
    """One scan's use of the cache: look files up, store what was read, and
    afterwards forget files under the scanned folder that are gone."""

    def __init__(self, root):
        self.root = os.path.normpath(root)
        with _lock:
            self.entries = _load()
            self.by_identity = {(e.get("dev"), e.get("ino"), e.get("size"), e.get("mtime")): p
                                for p, e in self.entries.items() if e.get("ino")}
        self.seen = set()
        self.hits = self.misses = 0

    def get(self, path, s):
        """The cached read of this file, or None if it has to be read."""
        self.seen.add(path)
        e = self.entries.get(path)
        if e and _same(e, s):
            self.hits += 1
            return e
        old = self.by_identity.get((s["dev"], s["ino"], s["size"], s["mtime"]))
        if old and old != path:
            e = self.entries.get(old)
            if e and _same(e, s, moved=True):
                self.hits += 1
                e = dict(e, **s)
                self.entries[path] = e
                return e
        self.misses += 1
        return None

    def put(self, path, s, read):
        e = dict(read, **s)
        self.entries[path] = e
        return e

    def save(self):
        """Drop entries for files under this root that weren't seen, and write."""
        prefix = self.root + os.sep
        with _lock:
            for p in [p for p in self.entries
                      if p.startswith(prefix) and p not in self.seen]:
                del self.entries[p]
            tmp = _file() + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"version": __version__, "entries": self.entries}, fh)
                os.replace(tmp, _file())
            except OSError:
                pass                    # a cache that can't be saved is just slower


def clear():
    """Forget everything, so the next scan reads every file."""
    global _entries
    with _lock:
        _entries = {}
        try:
            os.remove(_file())
        except OSError:
            pass
