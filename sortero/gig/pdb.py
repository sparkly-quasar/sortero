"""Read a rekordbox USB export: which tracks it lists, and where on the stick.

export.pdb is the DeviceSQL database rekordbox writes to PIONEER/rekordbox/
on a stick, and what a player browses in rekordbox mode. The parsing is
crate-digger's (see sortero/vendor/rekordbox_pdb.py); this only walks the
tables a gig check needs.

Each table is a chain of pages. A page holds row groups of up to sixteen
rows, and a flag per row says whether it is still present: rekordbox marks
deleted rows rather than removing them, so absent rows are skipped.
"""
import os
from dataclasses import dataclass, field

from kaitaistruct import KaitaiStream

from ..vendor.rekordbox_pdb import RekordboxPdb

RELATIVE = os.path.join("PIONEER", "rekordbox", "export.pdb")
# Newer rekordbox versions also write these next to export.pdb. Newer
# players can read them instead; export.pdb is what every player reads.
EXTRA = ("exportExt.pdb", "exportLibrary.db")
MAX_PAGES = 1 << 20     # a damaged chain must not loop forever


class ExportError(Exception):
    pass


@dataclass
class Track:
    id: int
    path: str           # as rekordbox wrote it: "/Contents/Artist/Album/file.mp3"
    title: str
    size: int           # bytes, as exported


@dataclass
class Export:
    tracks: list
    playlists: dict = field(default_factory=dict)   # name -> [track id, ...] in order
    menu: int = 0       # categories in the browse menu (the columns table)


def _text(s):
    return s.body.text if s is not None else ""


def _rows(db, kind):
    """Every present row of one table, in page order."""
    for table in db.tables:
        if table.type != kind:
            continue
        ref, last, seen = table.first_page, table.last_page.index, set()
        while ref.index not in seen and len(seen) < MAX_PAGES:
            seen.add(ref.index)
            page = ref.body
            if page.is_data_page:
                for group in page.row_groups:
                    for row in group.rows:
                        if row.present:
                            yield row.body
            if ref.index == last:
                break
            ref = page.next_page


def read(path):
    """The tracks and playlists in an export.pdb. Raises ExportError if it can't be read."""
    try:
        with open(path, "rb") as fh:
            db = RekordboxPdb(False, KaitaiStream(fh))
            tracks = [Track(r.id, _text(r.file_path), _text(r.title), r.file_size)
                      for r in _rows(db, RekordboxPdb.PageType.tracks)]
            names = {r.id: _text(r.name) for r in _rows(db, RekordboxPdb.PageType.playlist_tree)
                     if not r.is_folder}
            entries = sorted((r.playlist_id, r.entry_index, r.track_id)
                             for r in _rows(db, RekordboxPdb.PageType.playlist_entries))
            menu = sum(1 for _ in _rows(db, RekordboxPdb.PageType.columns))
    except OSError as e:
        raise ExportError(f"couldn't open it ({e.strerror})")
    except Exception as e:
        raise ExportError(f"it isn't a database rekordbox wrote, or it's damaged ({e})")
    playlists = {name: [] for name in names.values()}
    for pid, _, tid in entries:
        if pid in names:
            playlists[names[pid]].append(tid)
    return Export(tracks, playlists, menu)


def on_stick(stick, track_path):
    """Where a track path from the export lives on a mounted stick."""
    return os.path.join(stick, *[p for p in track_path.split("/") if p])
