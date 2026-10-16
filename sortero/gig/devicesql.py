"""Write export.pdb, the DeviceSQL database a rekordbox stick carries and every Pioneer player reads.

Ported from openboxxx's writer (github.com/sparkly-quasar/openboxxx,
libopenboxxx-export/src/pdb, GPL-2.0-or-later), which was in turn written from
crate-digger's description of the format (rekordbox_pdb.ksy, the same one
sortero/vendor/rekordbox_pdb.py is generated from) and checked against a real
rekordbox export. Everything is little-endian.

The file is 4096-byte pages. Page 0 is the header: the table list, each
table's first and last page. Every other page belongs to one table and holds
rows packed forward from 0x28, with an index growing back from the end of the
page in groups of sixteen: each group is 36 bytes, sixteen 2-byte row offsets,
a 2-byte "present" mask and 2 spare bytes. A table's pages chain to the next.

All twenty standard tables are written, in rekordbox's order. Laid out the way
a real rekordbox export is (compared page by page with the one in
tests/fixtures), which goes beyond what openboxxx writes:

  * each table starts with an index page (flags 0x64) listing its data pages,
    and a table with no rows is that index page alone
  * each table's chain ends on a spare "empty candidate" page, which the file
    header names and which is written as a blank page
  * rows start on 4-byte boundaries, and index_shift steps by 0x20 a row
  * tracks say autoload_hot_cues "ON", and when they were added and analysed

A few track-row fields only rekordbox itself reads are copied from a real
export (TRACK_* below) and may differ between rekordbox versions.
"""
import struct
from dataclasses import dataclass, field

PAGE = 4096
HEADER = 0x28
GROUP = 16
DATA_PAGE_FLAGS = 0x34
INDEX_PAGE_FLAGS = 0x64
INDEX_ENTRIES = 1004            # data pages one index page can list
NO_PAGE = 0x03FFFFFF
EMPTY_ENTRY = 0x1FFFFFF8

(TRACKS, GENRES, ARTISTS, ALBUMS, LABELS, KEYS, COLORS, PLAYLIST_TREE, PLAYLIST_ENTRIES,
 UNKNOWN9, UNKNOWN10, HISTORY_PLAYLISTS, HISTORY_ENTRIES, ARTWORK, UNKNOWN14, UNKNOWN15,
 COLUMNS, UNKNOWN17, UNKNOWN18, HISTORY) = range(20)

TRACK_BITMASK = 0xC0700
TRACK_U7, TRACK_U8 = 44990, 54115
TRACK_U26, TRACK_U29, TRACK_U30 = 41, 11, 3
TRACK_STRINGS = 21
# Which of a track row's 21 strings is which (crate-digger's track_row).
S_AUTOLOAD, S_DATE_ADDED = 7, 10
S_ANALYZE_PATH, S_ANALYZE_DATE, S_COMMENT, S_TITLE, S_FILENAME, S_FILE_PATH = \
    14, 15, 16, 17, 19, 20

# rekordbox's eight track colours, ids 1-8.
PALETTE = (("Pink", (0xF8, 0x70, 0xF8)), ("Red", (0xF8, 0x70, 0x90)),
           ("Orange", (0xF8, 0xA0, 0x30)), ("Yellow", (0xF8, 0xE3, 0x31)),
           ("Green", (0x1E, 0xE0, 0x00)), ("Aqua", (0x16, 0xC0, 0xF8)),
           ("Blue", (0x01, 0x50, 0xF8)), ("Purple", (0x98, 0x08, 0xF8)))


@dataclass
class Beat:
    number: int                     # 1-4, its place in the bar
    tempo: int                      # BPM x 100
    ms: int


@dataclass
class Cue:
    ms: int
    hot: int = -1                   # 0-7 for hot cues A-H, -1 for a memory cue
    loop_end_ms: int = None
    name: str = ""


@dataclass
class Track:
    id: int
    title: str
    file_path: str                  # on the stick: /Contents/Artist/Album/file.mp3
    analyze_path: str = ""          # /PIONEER/USBANLZ/P001/00000001/ANLZ0000.DAT
    artist: str = ""
    album: str = ""
    genre: str = ""
    key: str = ""
    comment: str = ""
    year: int = 0
    track_number: int = 0
    duration: int = 0               # seconds
    bitrate: int = 0                # kbps
    sample_rate: int = 0
    file_size: int = 0
    bpm: float = 0.0
    rating: int = 0                 # 0-5
    color: tuple = None             # (r, g, b), mapped to the nearest palette colour
    beats: list = field(default_factory=list)
    cues: list = field(default_factory=list)


@dataclass
class Playlist:
    id: int
    name: str
    parent: int = 0                 # 0: at the top
    folder: bool = False
    tracks: list = field(default_factory=list)      # track ids, in order


# ------------------------------------------------------------------ strings
def string(s):
    """A DeviceSQL string: short ASCII, long ASCII, or long UTF-16LE."""
    s = s or ""
    if s.isascii():
        b = s.encode("ascii")
        if len(b) <= 126:
            return bytes([(len(b) + 1) * 2 + 1]) + b
        return struct.pack("<BHB", 0x40, len(b) + 4, 0) + b
    b = s.encode("utf-16-le")
    return struct.pack("<BHB", 0x90, len(b) + 4, 0) + b


def color_id(rgb):
    """The nearest of rekordbox's eight colours, 0 for none."""
    if not rgb:
        return 0
    return 1 + min(range(8), key=lambda i: sum((a - b) ** 2
                                                for a, b in zip(rgb, PALETTE[i][1])))


def rating(stars):
    return 0 if stars <= 0 else 255 if stars >= 5 else stars * 51


# --------------------------------------------------------------------- rows
def track_row(t, artist, album, genre, key, shift=0, today=""):
    tempo = t.beats[0].tempo if t.beats else int(round(t.bpm * 100))
    head = struct.pack(
        "<HHIIIIIHHIIIIIIIIIIIIHHHHHHBBHH",
        0x24, shift, TRACK_BITMASK, t.sample_rate, 0, t.file_size & 0xFFFFFFFF, 0,
        TRACK_U7, TRACK_U8, 0, key, 0, 0, 0, t.bitrate, t.track_number, tempo,
        genre, album, artist, t.id, 0, 0, t.year, 16, min(t.duration, 0xFFFF),
        TRACK_U26, color_id(t.color), rating(t.rating), TRACK_U29, TRACK_U30)
    strings = [""] * TRACK_STRINGS
    strings[S_AUTOLOAD] = "ON"
    strings[S_DATE_ADDED] = strings[S_ANALYZE_DATE] = today
    strings[S_ANALYZE_PATH] = t.analyze_path
    strings[S_COMMENT] = t.comment
    strings[S_TITLE] = t.title
    strings[S_FILENAME] = t.file_path.rsplit("/", 1)[-1]
    strings[S_FILE_PATH] = t.file_path
    offsets, body = [], b""
    at = len(head) + 2 * TRACK_STRINGS
    for s in strings:
        offsets.append(at + len(body))
        body += string(s)
    return head + struct.pack(f"<{TRACK_STRINGS}H", *offsets) + body


def artist_row(id_, name, shift=0):
    return struct.pack("<HHIBB", 0x60, shift, id_, 0x03, 0x0A) + string(name)


def album_row(id_, artist, name, shift=0):
    return struct.pack("<HHIIIIBB", 0x80, shift, 0, artist, id_, 0, 0x03, 0x16) + string(name)


def genre_row(id_, name):
    return struct.pack("<I", id_) + string(name)


def key_row(id_, name):
    return struct.pack("<II", id_, id_) + string(name)


def color_row(id_, name):
    return bytes(5) + struct.pack("<HB", id_, 0) + string(name)


def playlist_tree_row(p, order):
    return struct.pack("<IIIII", p.parent, 0, order, p.id, 1 if p.folder else 0) + string(p.name)


def playlist_entry_row(index, track, playlist):
    return struct.pack("<III", index, track, playlist)


# -------------------------------------------------------------------- pages
def index_bytes(n):
    """Bytes of row index `n` rows need: 36 per full group, 4 + 2 per row in a partial one."""
    full, rem = divmod(n, GROUP)
    return full * 36 + (4 + 2 * rem if rem else 0)


class Page:
    def __init__(self, kind):
        self.kind, self.rows, self.used = kind, [], 0

    def add(self, row):
        """Add a row if it fits; False (and nothing changed) if it doesn't."""
        row += bytes(-len(row) % 4)                 # rows start on 4-byte boundaries
        if HEADER + self.used + len(row) + index_bytes(len(self.rows) + 1) > PAGE:
            return False
        self.rows.append(row)
        self.used += len(row)
        return True

    def bytes(self, index, next_index):
        page = bytearray(PAGE)
        pos, offsets = HEADER, []
        for row in self.rows:
            offsets.append(pos - HEADER)
            page[pos:pos + len(row)] = row
            pos += len(row)
        n = len(self.rows)
        for r, ofs in enumerate(offsets):
            g, local = divmod(r, GROUP)
            struct.pack_into("<H", page, PAGE - g * 36 - (6 + 2 * local), ofs)
        for g in range((n + GROUP - 1) // GROUP):
            count = min(GROUP, n - g * GROUP)
            struct.pack_into("<H", page, PAGE - g * 36 - 4, (1 << count) - 1)
        struct.pack_into("<III", page, 0x04, index, self.kind, next_index)
        packed = (n & 0x1FFF) | ((n & 0x7FF) << 13)        # num_rows_small, num_rows_large
        page[0x18:0x1B] = packed.to_bytes(3, "little")
        page[0x1B] = DATA_PAGE_FLAGS
        struct.pack_into("<HH", page, 0x1C, PAGE - HEADER - self.used - index_bytes(n),
                         self.used)
        return bytes(page)


def index_page(index, kind, data_pages):
    """The page a table starts with: which pages hold its rows."""
    page = bytearray(PAGE)
    first = data_pages[0] if data_pages else NO_PAGE
    n = len(data_pages)
    struct.pack_into("<III", page, 0x04, index, kind, first)
    page[0x1B] = INDEX_PAGE_FLAGS
    struct.pack_into("<HHHH", page, 0x20, 0x1FFF, 0x1FFF, INDEX_ENTRIES, n)
    struct.pack_into("<IIIIHH", page, HEADER, index, first, NO_PAGE, 0, n, 0x1FFF)
    entries = [p << 3 for p in data_pages] + [EMPTY_ENTRY] * (INDEX_ENTRIES - n)
    struct.pack_into(f"<{INDEX_ENTRIES}I", page, HEADER + 20, *entries)
    return bytes(page)


# --------------------------------------------------------------------- file
def build(tracks, playlists, today=None):
    """export.pdb's bytes, for these tracks and playlists (Track and Playlist lists)."""
    import time
    today = today or time.strftime("%Y-%m-%d")
    ids = {"artist": {}, "album": {}, "genre": {}, "key": {}}

    def intern(kind, value):
        if not value:
            return 0
        return ids[kind].setdefault(value, len(ids[kind]) + 1)

    track_rows, album_artist = [], {}
    for n, t in enumerate(tracks):
        a = intern("artist", t.artist)
        al = intern("album", t.album)
        if al and al not in album_artist:
            album_artist[al] = a
        track_rows.append(track_row(t, a, al, intern("genre", t.genre), intern("key", t.key),
                                    (n * 0x20) & 0xFFFF, today))

    def table(kind, make):
        return [make(i, name) for name, i in sorted(ids[kind].items(), key=lambda kv: kv[1])]

    tree_rows, entry_rows = [], []
    for order, p in enumerate(playlists):
        tree_rows.append(playlist_tree_row(p, order))
        entry_rows += [playlist_entry_row(i, tid, p.id) for i, tid in enumerate(p.tracks, 1)]

    rows = {
        TRACKS: track_rows,
        GENRES: table("genre", genre_row),
        ARTISTS: table("artist", lambda i, n: artist_row(i, n, ((i - 1) * 0x20) & 0xFFFF)),
        ALBUMS: table("album", lambda i, n: album_row(i, album_artist.get(i, 0), n,
                                                      ((i - 1) * 0x20) & 0xFFFF)),
        KEYS: table("key", key_row),
        COLORS: [color_row(i + 1, name) for i, (name, _) in enumerate(PALETTE)],
        PLAYLIST_TREE: tree_rows,
        PLAYLIST_ENTRIES: entry_rows,
    }
    body, spans, next_index = [], [], 1
    for kind in range(20):
        pages = []
        for row in rows.get(kind, []):
            if not pages or not pages[-1].add(row):
                pages.append(Page(kind))
                if not pages[-1].add(row):
                    raise ValueError("a row too big for a page")
        if len(pages) > INDEX_ENTRIES:
            raise ValueError("too many rows for one index page")
        index = next_index
        data = list(range(index + 1, index + 1 + len(pages)))
        spare = index + 1 + len(pages)
        body.append(index_page(index, kind, data))
        for i, page in enumerate(pages):
            body.append(page.bytes(data[i], data[i + 1] if i + 1 < len(data) else spare))
        body.append(bytes(PAGE))                    # the empty candidate
        spans.append((kind, spare, index, data[-1] if data else index))
        next_index = spare + 1

    header = struct.pack("<IIIIIII", 0, PAGE, len(spans), next_index, 0, 1, 0)
    for kind, spare, first, last in spans:
        header += struct.pack("<IIII", kind, spare, first, last)
    return header.ljust(PAGE, b"\0") + b"".join(body)
