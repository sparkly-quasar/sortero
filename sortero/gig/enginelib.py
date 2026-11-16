"""Write and read m.db, the Engine DJ library Denon's players read.

Denon's players (SC6000, SC5000, PRIME 4 and the rest, on Engine OS) list a
stick's playlists, cues and grids from an Engine library on it:

  Engine Library/Database2/m.db       the library: tracks, playlists, performance data
  Engine Library/Database2/hm.db ...  history and others, which Engine makes; left alone
  Contents/Artist/Album/file.mp3      the audio, the same files export.pdb names

A track's path is relative to the Engine Library folder ("../Contents/..."),
so one stick carries both rekordbox's libraries and this one over a single
copy of the audio. rb2engine and PenBridge write sticks the same way.

m.db is plain SQLite. The schema is libdjinterop's (github.com/xsco/libdjinterop,
src/djinterop/engine/schema/schema_3_0_2.cpp, LGPL-3.0-or-later): SCHEMA below
is its create() taken statement by statement from that file, triggers and
views included, because Denon's own note on third-party tools says Engine
refuses a database whose schema isn't the one it expects. The blob layouts
are libdjinterop's encoders (src/djinterop/engine/v2/*_blob.cpp); the grid's
-4 convention and the cue-to-pad policy follow rb2engine (MIT). See
docs/research/writing-engine-library.md.

Checked against a library Engine itself wrote (a Denon player's, on a stick it
had browsed as folders, schema 3.0.2, September 2026): the same tables, views,
triggers and indexes, differing only in line breaks; beatData, quickCues and
loops laid out byte for byte as here. Where Engine differed from libdjinterop,
this follows Engine: trackData has 24 more zero bytes (68 in all) and -1 for
no key, the overview is spaced samples / 1024 and "none" is four zero bytes,
and artwork is a 256 x 256 JPEG in Engine Library/Artwork, named for the
SHA-1 of the picture embedded in the audio, with that hash (20 bytes) in
AlbumArt and no image in the row.

Which schema: 3.0.2, as the research recommends. It's what Engine DJ 4.5 and
5.0 write and what current firmware (Engine OS 4.5 and later) reads as it
is, so a player on today's firmware uses the library as Sortero wrote it
rather than migrating it first. The cost is firmware older than 4.5, which
may refuse it. The 2.x schemas (Engine OS 3) lay out performance data
differently and aren't written. A counterpoint for when this meets real
players: rb2engine writes 3.0.1, because Engine migrates a library upward
(Engine DJ 4.3 turned a 3.0.1 stick into 3.0.2) but never back down, so the
older schema reaches more firmware. Switching would mean taking 3.0.1's DDL
from libdjinterop too (it lacks Track.albumArtSourceHash).

Not yet tried on a real Denon player. Open questions (the research's list):
whether an SC6000 / PRIME 4 on Engine OS 4.x and 5.x accepts a 3.0.2 m.db
Engine DJ didn't write; whether cues land on the beat on MP3s (encoder
delay); what currentPlayedIndiciator, the loudness values (Engine writes
its own, here 0) and the grid's unknown field must be; and whether the
players need hm.db, the other Database2 files and the per-track
Database2/OverviewData/<library uuid>/<track id>.rgb files Engine writes,
or make them.
"""
import base64, math, os, random, re, sqlite3, struct, tempfile, time, uuid as uuidlib, zlib
from dataclasses import dataclass, field

FOLDER = "Engine Library"
DATABASE = os.path.join(FOLDER, "Database2", "m.db")
OLD_DATABASE = os.path.join(FOLDER, "m.db")         # Engine 1.x, which Engine OS 4 doesn't read
SCHEMA_VERSION = (3, 0, 2)
PADS = LOOPS = 8
OVERVIEW_POINTS = 1024
NO_ART = 1                      # the AlbumArt row the schema makes, for "no picture"
ARTWORK = os.path.join(FOLDER, "Artwork")
NO_KEY = -1                     # trackData's key when there's none, as Engine writes it
TRACK_DATA_EXTRA = bytes(24)    # Engine's own trackData ends with 24 zero bytes
NO_OVERVIEW = bytes(4)          # Engine's own "no overview": an empty qCompress
NO_NEXT = 0                     # end of a nextListId / nextEntityId chain
EMPTY = -1.0                    # an empty pad or loop
BEAT_DATA_EXTRA = bytes(9)      # "real beat data seems to have 9 additional zero bytes" (libdjinterop)
# Pad colours (A R G B). Gig prep's XML doesn't carry cue colours, so pads get
# the colours a Pioneer player gives hot cues A-D (anlz.DEFAULT_COLOUR), twice.
PAD_COLOURS = ((255, 0xFF, 0x00, 0x17), (255, 0x00, 0xC4, 0xFF),
               (255, 0x33, 0xFF, 0x00), (255, 0x4D, 0x00, 0xFF)) * 2
LOOP_COLOUR = (255, 0xFF, 0x8C, 0x00)


class EngineError(Exception):
    pass


# ------------------------------------------------------------------ schema
# libdjinterop's schema_3_0_2::create(), in order, as the C++ string literals join up.
SCHEMA = (
    'CREATE TABLE Information ( \tid INTEGER PRIMARY KEY AUTOINCREMENT, \tuuid TEXT, \tschemaVersionMajor INTEGER, \tschemaVersionMinor INTEGER, \tschemaVersionPatch INTEGER, \tcurrentPlayedIndiciator INTEGER, \tlastRekordBoxLibraryImportReadCounter INTEGER);',
    'CREATE TABLE AlbumArt ( \tid INTEGER PRIMARY KEY AUTOINCREMENT, \thash TEXT, \talbumArt BLOB );',
    'CREATE TABLE Pack ( \tid INTEGER PRIMARY KEY AUTOINCREMENT, \tpackId TEXT, \tchangeLogDatabaseUuid TEXT, \tchangeLogId INTEGER, \tlastPackTime DATETIME );',
    'CREATE TABLE Track ( \tid INTEGER PRIMARY KEY AUTOINCREMENT, \tplayOrder INTEGER, \tlength INTEGER, \tbpm INTEGER, \tyear INTEGER, \tpath TEXT, \tfilename TEXT, \tbitrate INTEGER, \tbpmAnalyzed REAL, \talbumArtId INTEGER, \tfileBytes INTEGER, \ttitle TEXT, \tartist TEXT, \talbum TEXT, \tgenre TEXT, \tcomment TEXT, \tlabel TEXT, \tcomposer TEXT, \tremixer TEXT, \tkey INTEGER, \trating INTEGER, \talbumArt TEXT, \ttimeLastPlayed DATETIME, \tisPlayed BOOLEAN, \tfileType TEXT, \tisAnalyzed BOOLEAN, \tdateCreated DATETIME, \tdateAdded DATETIME, \tisAvailable BOOLEAN, \tisMetadataOfPackedTrackChanged BOOLEAN, \tisPerfomanceDataOfPackedTrackChanged BOOLEAN, \tplayedIndicator INTEGER, \tisMetadataImported BOOLEAN, \tpdbImportKey INTEGER, \tstreamingSource TEXT, \turi TEXT, \tisBeatGridLocked BOOLEAN, \toriginDatabaseUuid TEXT, \toriginTrackId INTEGER, \tstreamingFlags INTEGER, \texplicitLyrics BOOLEAN, \tlastEditTime DATETIME, albumArtSourceHash CHAR(40), \tCONSTRAINT C_originDatabaseUuid_originTrackId UNIQUE (originDatabaseUuid, originTrackId), \tCONSTRAINT C_path UNIQUE (path), \tFOREIGN KEY (albumArtId) REFERENCES AlbumArt (id) ON DELETE RESTRICT );',
    'CREATE TABLE PerformanceData ( \ttrackId INTEGER PRIMARY KEY, \ttrackData BLOB, \toverviewWaveFormData BLOB, \tbeatData BLOB, \tquickCues BLOB, \tloops BLOB, \tthirdPartySourceId INTEGER, \tactiveOnLoadLoops INTEGER, \tFOREIGN KEY(trackId) REFERENCES Track(id) ON DELETE CASCADE ON UPDATE CASCADE );',
    'CREATE TABLE Playlist ( \tid INTEGER PRIMARY KEY AUTOINCREMENT, \ttitle TEXT, \tparentListId INTEGER, \tisPersisted BOOLEAN, \tnextListId INTEGER, \tlastEditTime DATETIME, \tisExplicitlyExported BOOLEAN, \tCONSTRAINT C_NAME_UNIQUE_FOR_PARENT UNIQUE (title, parentListId), \tCONSTRAINT C_NEXT_LIST_ID_UNIQUE_FOR_PARENT UNIQUE (parentListId, nextListId) );',
    'CREATE TABLE PlaylistEntity ( \tid INTEGER PRIMARY KEY AUTOINCREMENT, \tlistId INTEGER, \ttrackId INTEGER, \tdatabaseUuid TEXT, \tnextEntityId INTEGER, \tmembershipReference INTEGER, \tCONSTRAINT C_NAME_UNIQUE_FOR_LIST UNIQUE (listId, databaseUuid, trackId), \tFOREIGN KEY (listId) REFERENCES Playlist (id) ON DELETE CASCADE );',
    'CREATE TABLE PreparelistEntity ( \tid INTEGER PRIMARY KEY AUTOINCREMENT, \ttrackId INTEGER, \ttrackNumber INTEGER, \tFOREIGN KEY (trackId) REFERENCES Track (id) ON DELETE CASCADE );',
    'CREATE TABLE Smartlist ( \tlistUuid TEXT NOT NULL PRIMARY KEY, \ttitle TEXT, \tparentPlaylistPath TEXT, \tnextPlaylistPath TEXT, \tnextListUuid TEXT, \trules TEXT, \tlastEditTime DATETIME, \tCONSTRAINT C_NAME_UNIQUE_FOR_PARENT UNIQUE (title, parentPlaylistPath), \tCONSTRAINT C_NEXT_LIST_UNIQUE_FOR_PARENT UNIQUE (parentPlaylistPath, nextPlaylistPath, nextListUuid) );',
    'CREATE VIEW ChangeLog (id, trackId) AS SELECT 0, 0 WHERE FALSE;',
    'CREATE VIEW PlaylistAllParent AS WITH FindAllParent AS ( \tSELECT id, parentListId FROM Playlist \tUNION ALL \tSELECT recursiveCTE.id, Plist.parentListId FROM Playlist Plist \tINNER JOIN FindAllParent recursiveCTE \tON recursiveCTE.parentListId = Plist.id ) SELECT * FROM FindAllParent;',
    'CREATE VIEW PlaylistAllChildren AS WITH FindAllChild AS ( SELECT id, id as childListId FROM Playlist UNION ALL SELECT recursiveCTE.id, Plist.id FROM Playlist Plist INNER JOIN FindAllChild recursiveCTE ON recursiveCTE.childListId = Plist.parentListId ) SELECT * FROM FindAllChild WHERE id <> childListId;',
    "CREATE VIEW PlaylistPath AS WITH RECURSIVE Heirarchy AS ( \tSELECT id AS child, parentListId AS parent, title AS name, 1 AS depth FROM Playlist \tUNION ALL \tSELECT child, parentListId AS parent, title AS name, h.depth + 1 AS depth FROM Playlist c \tJOIN Heirarchy h ON h.parent = c.id \tORDER BY depth DESC ), OrderedList AS ( \tSELECT id , nextListId, 1 AS position \tFROM Playlist \tWHERE nextListId = 0 \tUNION ALL \tSELECT c.id , c.nextListId , l.position + 1 \tFROM Playlist c \tINNER JOIN OrderedList l \tON c.nextListId = l.id ), NameConcat AS ( \tSELECT \t\tchild AS id, \t\tGROUP_CONCAT(name ,';') || ';' AS path \tFROM \t( \t\tSELECT child, name \t\tFROM Heirarchy \t\tORDER BY depth DESC \t) \tGROUP BY child ) SELECT \tid, \tpath, \tROW_NUMBER() OVER \t( \t\tORDER BY \t\t(SELECT COUNT(*) FROM (SELECT * FROM Heirarchy WHERE child = id) ) DESC, \t\t(SELECT position FROM OrderedList ol WHERE ol.id = c.id) ASC \t) AS position FROM Playlist c LEFT JOIN NameConcat g USING (id);",
    "CREATE TRIGGER trigger_after_insert_Pack_timestamp AFTER INSERT ON Pack FOR EACH ROW WHEN NEW.lastPackTime IS NULL BEGIN \tUPDATE Pack SET lastPackTime = strftime('%s') WHERE ROWID = NEW.ROWID; END;",
    'CREATE TRIGGER trigger_after_insert_Pack_changeLogId AFTER INSERT ON Pack FOR EACH ROW WHEN NEW.changeLogId = 0 BEGIN \tUPDATE Pack SET changeLogId = 1 WHERE ROWID = NEW.ROWID; END;',
    "CREATE TRIGGER trigger_after_insert_Track_check_id AFTER INSERT ON Track \tWHEN NEW.id <= (SELECT seq FROM sqlite_sequence WHERE name = 'Track') BEGIN \tSELECT RAISE(ABORT, 'Recycling deleted track id''s are not allowed'); END;",
    "CREATE TRIGGER trigger_after_update_Track_check_Id BEFORE UPDATE ON Track \tWHEN NEW.id <> OLD.id BEGIN \tSELECT RAISE(ABORT, 'Changing track id''s are not allowed'); END;",
    "CREATE TRIGGER trigger_after_insert_Track_fix_origin AFTER INSERT ON Track \tWHEN IFNULL(NEW.originTrackId, 0) = 0 \tOR IFNULL(NEW.originDatabaseUuid, '') = '' BEGIN \tUPDATE Track SET \t\toriginTrackId = NEW.id, \t\toriginDatabaseUuid = (SELECT uuid FROM Information) \tWHERE track.id = NEW.id; END;",
    "CREATE TRIGGER trigger_after_update_Track_fix_origin AFTER UPDATE ON Track \tWHEN IFNULL(NEW.originTrackId, 0) = 0 \tOR IFNULL(NEW.originDatabaseUuid, '') = '' BEGIN \tUPDATE Track SET \t\toriginTrackId = NEW.id, \t\toriginDatabaseUuid = (SELECT uuid FROM Information) \tWHERE track.id = NEW.id; END;",
    "CREATE TRIGGER trigger_after_update_only_Track_timestamp \tAFTER UPDATE OF\tlength, bpm, year, filename, bitrate, bpmAnalyzed, albumArtId, \ttitle, artist, album, genre, comment, label, composer, remixer, key, rating, albumArt, \tfileType, isAnalyzed, isBeatgridLocked, explicitLyrics \tON Track \tFOR EACH ROW BEGIN \tUPDATE Track SET lastEditTime = strftime('%s') WHERE ROWID=NEW.ROWID; END;",
    'CREATE TRIGGER trigger_after_insert_Track_insert_performance_data AFTER INSERT ON Track BEGIN \tINSERT INTO PerformanceData(trackId) VALUES(NEW.id); END;',
    "CREATE TRIGGER trigger_PerformanceData_after_update_Track_timestamp \tAFTER UPDATE OF trackData, isAnalyzed, overviewWaveFormData, beatData, quickCues, loops, activeOnLoadLoops \tON PerformanceData \tFOR EACH ROW BEGIN \tUPDATE Track \tSET lastEditTime = strftime('%s') \tWHERE id = NEW.trackId; END;",
    'CREATE TRIGGER trigger_before_insert_List BEFORE INSERT ON Playlist FOR EACH ROW BEGIN \tUPDATE Playlist SET \t\tnextListId = -(1 + nextListId) \tWHERE nextListId = NEW.nextListId \tAND parentListId = NEW.parentListId; END;',
    'CREATE TRIGGER trigger_after_insert_List AFTER INSERT ON Playlist FOR EACH ROW BEGIN \tUPDATE Playlist SET \t\tnextListId = NEW.id \tWHERE nextListId = -(1 + NEW.nextListId) \tAND parentListId = NEW.parentListId; END;',
    'CREATE TRIGGER trigger_after_delete_List AFTER DELETE ON Playlist FOR EACH ROW BEGIN \tUPDATE Playlist SET \t\tnextListId = OLD.nextListId \tWHERE nextListId = OLD.id; \tDELETE FROM Playlist \tWHERE parentListId = OLD.id; END;',
    'CREATE TRIGGER trigger_after_update_isPersistParent AFTER UPDATE ON Playlist \tWHEN (old.isPersisted = 0 \tAND new.isPersisted = 1) \tOR (old.parentListId != new.parentListId \tAND new.isPersisted = 1) BEGIN \tUPDATE Playlist SET \t\tisPersisted = 1 \tWHERE id IN (SELECT parentListId FROM PlaylistAllParent WHERE id=new.id); END;',
    'CREATE TRIGGER trigger_after_update_isPersistChild AFTER UPDATE ON Playlist \tWHEN old.isPersisted = 1 \tAND new.isPersisted = 0 BEGIN \tUPDATE Playlist SET \t\tisPersisted = 0 \tWHERE id IN (SELECT childListId FROM PlaylistAllChildren WHERE id=new.id); END;',
    'CREATE TRIGGER trigger_after_insert_isPersist AFTER INSERT ON Playlist \tWHEN new.isPersisted = 1 BEGIN \tUPDATE Playlist SET \t\tisPersisted = 1 \tWHERE id IN (SELECT parentListId FROM PlaylistAllParent WHERE id=new.id); END;',
    'CREATE TRIGGER trigger_before_delete_PlaylistEntity BEFORE DELETE ON PlaylistEntity WHEN OLD.trackId > 0 BEGIN \tUPDATE PlaylistEntity SET \t\tnextEntityId = OLD.nextEntityId \tWHERE nextEntityId = OLD.id \tAND listId = OLD.listId; END;',
    'CREATE INDEX index_AlbumArt_hash ON AlbumArt (hash);',
    'CREATE INDEX index_Track_filename ON Track (filename);',
    'CREATE INDEX index_Track_albumArtId ON Track (albumArtId);',
    'CREATE INDEX index_Track_uri ON Track (uri);',
    'CREATE INDEX index_Track_title ON Track(title);',
    'CREATE INDEX index_Track_length ON Track(length);',
    'CREATE INDEX index_Track_rating ON Track(rating);',
    'CREATE INDEX index_Track_year ON Track(year);',
    'CREATE INDEX index_Track_dateAdded ON Track(dateAdded);',
    'CREATE INDEX index_Track_genre ON Track(genre);',
    'CREATE INDEX index_Track_artist ON Track(artist);',
    'CREATE INDEX index_Track_album ON Track(album);',
    'CREATE INDEX index_Track_key ON Track(key);',
    'CREATE INDEX index_Track_bpmAnalyzed ON Track(CAST(bpmAnalyzed + 0.5 AS int));',
    'CREATE INDEX index_PlaylistEntity_nextEntityId_listId ON PlaylistEntity(nextEntityId, listId);',
    'CREATE INDEX index_PreparelistEntity_trackId ON PreparelistEntity (trackId);',
)


# ------------------------------------------------------------------- blobs
def compress(data):
    """Qt's qCompress, which Engine uses: the length (4 bytes, big-endian), then zlib."""
    return struct.pack(">I", len(data)) + zlib.compress(data)


def track_data(rate, samples, key):
    """trackData: sample rate, sample count, key, then average loudness low/mid/high
    (0, as libdjinterop writes when it doesn't know), then 24 zero bytes, as in the
    68-byte trackData Engine itself writes."""
    return compress(struct.pack(">dqiddd", rate, samples, NO_KEY if key is None else key,
                                0.0, 0.0, 0.0) + TRACK_DATA_EXTRA)


@dataclass
class Marker:
    samples: float              # where the beat is, in samples from the start
    index: int                  # which beat it is; downbeats are multiples of 4


def _grid(markers):
    out = struct.pack(">q", len(markers))
    for i, m in enumerate(markers):
        beats = markers[i + 1].index - m.index if i + 1 < len(markers) else 0
        out += struct.pack("<dqii", m.samples, m.index, beats, 0)
    return out


def beat_data(rate, samples, markers):
    """beatData: rate, samples, whether there's a grid, then the default grid and
    the adjusted one (the same here). The counts are big-endian and the markers
    little-endian; that's how Engine writes it."""
    body = (struct.pack(">ddB", rate, samples, 1 if markers else 0)
            + _grid(markers) + _grid(markers) + BEAT_DATA_EXTRA)
    return compress(body)


def _label(text):
    """A pad or loop label: UTF-8, at most 255 bytes, cut on a character."""
    raw = (text or "").encode("utf-8")
    while len(raw) > 255:
        text = text[:-1]
        raw = text.encode("utf-8")
    return bytes([len(raw)]) + raw


@dataclass
class Pad:
    samples: float = EMPTY
    label: str = ""
    colour: tuple = (0, 0, 0, 0)


@dataclass
class Loop:
    start: float = EMPTY
    end: float = EMPTY
    label: str = ""
    colour: tuple = (0, 0, 0, 0)


def quick_cues(pads, main_cue):
    """quickCues: eight pads (label, position, colour), then the main cue twice."""
    out = struct.pack(">q", len(pads))
    for p in pads:
        out += _label(p.label) + struct.pack(">d", p.samples) + bytes(p.colour)
    return compress(out + struct.pack(">dBd", main_cue, 1, main_cue))


def loops(slots):
    """loops: eight saved loops. Little-endian and not compressed, unlike the rest."""
    out = struct.pack("<q", len(slots))
    for s in slots:
        set_ = 1 if s.start >= 0 else 0
        out += _label(s.label) + struct.pack("<ddBB", s.start, s.end, set_, set_) + bytes(s.colour)
    return out


def overview(rate, samples, columns):
    """overviewWaveFormData: 1024 points of low, mid, high (0-255), and the loudest.

    `columns` is waveform.pwv7's bytes: low, mid, high per 1/150 s, 0-127. Each
    point is the loudest of the columns it spans, so transients survive."""
    n = len(columns) // 3
    if not n or not samples:
        return NO_OVERVIEW
    points = []
    for i in range(OVERVIEW_POINTS):
        start = i * n // OVERVIEW_POINTS
        end = min(max((i + 1) * n // OVERVIEW_POINTS, start + 1), n)
        span = columns[start * 3:end * 3]
        points.append(tuple(min(255, max(span[k::3]) * 2) for k in range(3)))
    per_point = samples / OVERVIEW_POINTS     # as Engine writes it (libdjinterop rounds first)
    loudest = tuple(max(p[k] for p in points) for k in range(3))
    return compress(struct.pack(">qqd", OVERVIEW_POINTS, OVERVIEW_POINTS, per_point)
                    + b"".join(bytes(p) for p in points) + bytes(loudest))


# ------------------------------------------------------- from Sortero's marks
def beat_grid(grids, rate, samples):
    """Engine's grid markers from tempo stretches (cues.Grid: first beat in seconds,
    BPM, which beat of the bar it is).

    One marker per tempo change. As Engine (and libdjinterop) want it, the first
    sits at beat -4, extrapolated back, and the last a beat past the end of the
    track, so a constant tempo is two markers. Beat numbers put downbeats on
    multiples of 4."""
    end = samples / rate if rate else 0
    grids = sorted((g for g in grids if g.bpm > 0 and g.start < end), key=lambda g: g.start)
    if not grids:
        return []
    first = grids[0]
    index = (int(first.beat) - 1) % 4  # the first beat's place in the bar
    marks = []                          # (seconds, index, period)
    for i, g in enumerate(grids):
        if i:
            prev = marks[-1]
            index += max(1, round((g.start - prev[0]) / prev[2]))
        marks.append((g.start, index, 60.0 / g.bpm))
    start, idx, period = marks[0]
    marks[0] = (start - (4 + idx) * period, -4, period)
    start, idx, period = marks[-1] if len(marks) > 1 else (first.start, index, 60.0 / first.bpm)
    beats = max(1, math.ceil((end - start) / period))
    marks.append((start + beats * period, idx + beats, period))
    return [Marker(s * rate, i) for s, i, _ in marks]


def pads_and_loops(cues, rate):
    """(eight Pads, eight Loops, main cue in samples, how many cues didn't fit).

    rb2engine's policy: a loop goes to a loop slot (hot-cue loops first, by pad,
    then the rest by time) and takes no pad; a hot cue keeps its pad; memory
    cues fill the pads left, in time order. The main cue is the first memory cue."""
    def at(ms):
        return ms * rate / 1000.0
    pads, slots, dropped = [None] * PADS, [], 0
    memory = []
    for c in sorted(cues, key=lambda c: c.ms):
        if c.loop_end_ms is not None:
            continue
        if 0 <= c.hot < PADS and pads[c.hot] is None:
            pads[c.hot] = Pad(at(c.ms), c.name, PAD_COLOURS[c.hot])
        else:
            memory.append(c)
    free = (i for i in range(PADS) if pads[i] is None)
    for c in memory:
        i = next(free, None)
        if i is None:
            dropped += 1
            continue
        pads[i] = Pad(at(c.ms), c.name, PAD_COLOURS[i])
    looped = [c for c in cues if c.loop_end_ms is not None]
    looped.sort(key=lambda c: (0, c.hot, c.ms) if 0 <= c.hot < PADS else (1, 0, c.ms))
    for c in looped:
        if len(slots) == LOOPS:
            dropped += 1
            continue
        slots.append(Loop(at(c.ms), at(c.loop_end_ms), c.name, LOOP_COLOUR))
    mains = [c.ms for c in cues if c.hot < 0 and c.loop_end_ms is None]
    main = at(min(mains)) if mains else 0.0
    return ([p or Pad() for p in pads], slots + [Loop()] * (LOOPS - len(slots)), main,
            dropped)


# libdjinterop's musical_key: round the circle of fifths, each major then its relative minor.
_PITCH = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def key(text):
    """Engine's key number (0 C, 1 Am, 2 G, ... 23 Dm) for a key written as Am,
    F#m, Gb, 8A (Camelot) or 1m (Open Key); None if it isn't one."""
    s = (text or "").strip().lower().replace("♯", "#").replace("♭", "b")
    m = re.fullmatch(r"(1[0-2]|0?[1-9])\s*([abdm])", s)
    if m:
        n, letter = int(m.group(1)), m.group(2)
        if letter in "ab":                          # Camelot: 8B is C, 8A is Am
            return 2 * ((n - 8) % 12) + (letter == "a")
        return 2 * ((n - 1) % 12) + (letter == "m")  # Open Key: 1d is C, 1m is Am
    m = re.fullmatch(r"([a-g])\s*([#b]?)\s*(m|min|minor|maj|major)?", s)
    if not m:
        return None
    pitch = (_PITCH[m.group(1)] + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12
    minor = (m.group(3) or "").startswith("m") and not (m.group(3) or "").startswith("ma")
    if minor:
        return 2 * ((pitch + 3) * 7 % 12) + 1      # by its relative major
    return 2 * (pitch * 7 % 12)


# ------------------------------------------------------------------- write
@dataclass
class Audio:
    """What Engine needs to know about a track's audio that export.pdb doesn't."""
    rate: int                   # samples per second
    samples: int                # samples in the whole track (per channel)
    grids: list = field(default_factory=list)       # cues.Grid
    overview: bytes = b""       # overview(), made as each track's waveform comes in


@dataclass
class Built:
    uuid: str
    tracks: int = 0
    playlists: int = 0
    repeats: list = field(default_factory=list)     # (playlist, track title) left out
    files: dict = field(default_factory=dict)       # stick path -> bytes: the artwork
    dropped_cues: list = field(default_factory=list)  # (track title, how many)


def _titles(playlists):
    """Each playlist's title, unique among its siblings (Engine insists) and
    without ";" (it separates the levels of a playlist's path)."""
    taken, out = set(), {}
    for p in playlists:
        base = (p.name or "Untitled").replace(";", ",").strip() or "Untitled"
        name, n = base, 2
        while (p.parent, name.casefold()) in taken:
            name, n = f"{base} ({n})", n + 1
        taken.add((p.parent, name.casefold()))
        out[p.id] = name
    return out


def art_file(digest):
    """Where Engine keeps a picture: Engine Library/Artwork/, named for AlbumArt.hash
    (the SHA-1 of the picture embedded in the audio, 20 bytes) in URL-safe base64.
    Found so on a stick Engine wrote; the AlbumArt row itself holds no image."""
    name = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return os.path.join(ARTWORK, name + ".jpg")


def build(path, tracks, playlists, audio, art=None, library_uuid=None, now=None):
    """Write m.db at `path`, all or nothing (make(), then the file whole). Returns a Built."""
    data, built = make(tracks, playlists, audio, art, library_uuid, now)
    stick = os.path.dirname(os.path.dirname(os.path.dirname(path)))
    for rel, content in list(built.files.items()) + [(os.path.relpath(path, stick), data)]:
        dest = os.path.join(stick, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest + ".sortero-tmp", "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(dest + ".sortero-tmp", dest)
    return built


def make(tracks, playlists, audio, art=None, library_uuid=None, now=None):
    """m.db's bytes, and a Built saying what's in it.

    `tracks` and `playlists` are the devicesql lists usbexport.plan() makes,
    `audio` {track id: Audio}, `art` {image id: (SHA-1 of the embedded picture,
    256 px JPEG)} for Track.artwork_id (artwork.Collector(engine=True).engine).
    The pictures go in Built.files, to be written beside m.db.
    `library_uuid` keeps the library's identity across rewrites, so a player's
    history stays with it.

    Made on this computer's disk, never on the stick: on FAT32 a file's inode
    number changes once it first holds data (macOS makes them up, FAT has
    none), and SQLite takes that for the database having been moved and
    refuses to write any more ("attempt to write a readonly database")."""
    with tempfile.TemporaryDirectory(prefix="sortero-engine-") as folder:
        tmp = os.path.join(folder, "m.db")
        built = _fill(tmp, tracks, playlists, audio, art or {}, library_uuid, now)
        with open(tmp, "rb") as fh:
            return fh.read(), built


def _fill(tmp, tracks, playlists, audio, art, library_uuid, now):
    now = int(now if now is not None else time.time())
    built = Built(library_uuid or str(uuidlib.uuid4()))
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        for statement in SCHEMA:
            con.execute(statement)
        major, minor, patch = SCHEMA_VERSION
        con.execute("INSERT INTO Information (uuid, schemaVersionMajor, schemaVersionMinor, "
                    "schemaVersionPatch, currentPlayedIndiciator, "
                    "lastRekordBoxLibraryImportReadCounter) VALUES (?, ?, ?, ?, ?, 0)",
                    (built.uuid, major, minor, patch, random.getrandbits(63)))
        con.execute("INSERT INTO AlbumArt VALUES (1, '', NULL)")
        art_rows = {}
        for image_id in sorted({t.artwork_id for t in tracks if t.artwork_id in art}):
            digest, jpeg = art[image_id]
            cur = con.execute("INSERT INTO AlbumArt (hash, albumArt) VALUES (?, NULL)",
                              (digest,))
            art_rows[image_id] = cur.lastrowid
            built.files[art_file(digest)] = jpeg
        ids = {}                        # Sortero's track id -> Engine's (1, 2, 3...)
        for t in tracks:
            a = audio[t.id]
            ids[t.id] = len(ids) + 1
            name = t.file_path.rsplit("/", 1)[-1]
            ext = os.path.splitext(name)[1].lstrip(".").lower()
            bpm = t.bpm or next((g.bpm for g in a.grids if g.bpm > 0), 0.0)
            k = key(t.key)
            art_id = art_rows.get(t.artwork_id, NO_ART)
            con.execute(
                "INSERT INTO Track (id, length, bpm, year, path, filename, bitrate, "
                "bpmAnalyzed, albumArtId, fileBytes, title, artist, album, genre, comment, "
                "key, rating, albumArt, isPlayed, fileType, isAnalyzed, dateCreated, "
                "dateAdded, isAvailable, isMetadataOfPackedTrackChanged, "
                "isPerfomanceDataOfPackedTrackChanged, isMetadataImported, pdbImportKey, "
                "isBeatGridLocked, originDatabaseUuid, originTrackId, streamingFlags, "
                "explicitLyrics) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "0, ?, 1, ?, ?, 1, 0, 0, 1, 0, 0, ?, ?, 0, 0)",
                (ids[t.id], t.duration or (a.samples // a.rate if a.rate else 0),
                 int(bpm) if bpm else None, t.year or None, ".." + t.file_path, name,
                 t.bitrate or None, bpm or None, art_id, t.file_size, t.title,
                 t.artist or None, t.album or None, t.genre or None, t.comment or None,
                 k, max(0, min(5, t.rating)) * 20, None,
                 ext, now, now, built.uuid, ids[t.id]))
            pads, slots, main, dropped = pads_and_loops(t.cues, a.rate)
            if dropped:
                built.dropped_cues.append((t.title, dropped))
            # The Track insert made this row (a trigger); fill in its blobs.
            con.execute(
                "UPDATE PerformanceData SET trackData = ?, overviewWaveFormData = ?, "
                "beatData = ?, quickCues = ?, loops = ?, thirdPartySourceId = NULL, "
                "activeOnLoadLoops = 0 WHERE trackId = ?",
                (track_data(a.rate, a.samples, k), a.overview or NO_OVERVIEW,
                 beat_data(a.rate, a.samples, beat_grid(a.grids, a.rate, a.samples)),
                 quick_cues(pads, main), loops(slots), ids[t.id]))
        built.tracks = len(ids)
        titles = _titles(playlists)
        edited = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))
        nxt, last = {}, {}               # playlist -> the sibling after it
        for p in playlists:
            if p.parent in last:
                nxt[last[p.parent]] = p.id
            last[p.parent] = p.id
        entity = 0
        for p in playlists:              # parents come before their children
            con.execute("INSERT INTO Playlist (id, title, parentListId, isPersisted, "
                        "nextListId, lastEditTime, isExplicitlyExported) "
                        "VALUES (?, ?, ?, 1, ?, ?, 1)",
                        (p.id, titles[p.id], p.parent, nxt.get(p.id, NO_NEXT), edited))
            seen, members = set(), []
            for tid in p.tracks:
                if tid not in ids:
                    continue
                if tid in seen:          # Engine has a track in a playlist once
                    title = next(t.title for t in tracks if t.id == tid)
                    built.repeats.append((titles[p.id], title))
                    continue
                seen.add(tid)
                members.append(ids[tid])
            rows = []
            for i, track_id in enumerate(members):
                entity += 1
                rows.append((entity, p.id, track_id, built.uuid,
                             entity + 1 if i + 1 < len(members) else NO_NEXT))
            con.executemany("INSERT INTO PlaylistEntity (id, listId, trackId, databaseUuid, "
                            "nextEntityId, membershipReference) VALUES (?, ?, ?, ?, ?, 0)", rows)
        built.playlists = len(playlists)
        con.commit()
    except sqlite3.Error as e:
        raise EngineError(f"couldn't write the Engine library ({e})") from e
    finally:
        con.close()
    return built


# -------------------------------------------------------------------- read
def _connect(path):
    if not os.path.isfile(path):
        raise EngineError("it isn't there")
    try:
        # Read-only, as Denon asks of other tools, and never makes a file.
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise EngineError(f"it can't be opened ({e})")


def info(path):
    """{"uuid", "schema" (major, minor, patch), "tracks", "playlists"} of an m.db."""
    con = _connect(path)
    try:
        row = con.execute("SELECT uuid, schemaVersionMajor, schemaVersionMinor, "
                          "schemaVersionPatch FROM Information ORDER BY id LIMIT 1").fetchone()
        tracks = con.execute("SELECT COUNT(*) FROM Track").fetchone()[0]
        lists = con.execute("SELECT COUNT(*) FROM Playlist").fetchone()[0]
    except sqlite3.Error as e:
        raise EngineError(f"it can't be read ({e})")
    finally:
        con.close()
    if not row:
        raise EngineError("it has no Information row")
    return {"uuid": row[0], "schema": tuple(row[1:]), "tracks": tracks, "playlists": lists}


def stick_path(path):
    """Where a Track.path (relative to the Engine Library folder) is on the stick,
    as "/Contents/...", or None if it points off the stick."""
    if not path:
        return None
    joined = os.path.normpath(os.path.join(FOLDER, path.replace("\\", "/")))
    if os.path.isabs(path) or joined.startswith(".."):
        return None
    return "/" + joined.replace(os.sep, "/")


def read(path):
    """[(track id, path on the stick or None, title, file size)] and
    {playlist title: [track id, in order]}."""
    con = _connect(path)
    try:
        tracks = [(i, stick_path(p), title, size) for i, p, title, size in con.execute(
            "SELECT id, path, title, fileBytes FROM Track ORDER BY id")]
        names = dict(con.execute("SELECT id, title FROM Playlist ORDER BY id"))
        lists = {n: [] for n in names.values()}
        chains = {}
        for eid, lid, tid, nxt in con.execute(
                "SELECT id, listId, trackId, nextEntityId FROM PlaylistEntity"):
            chains.setdefault(lid, {})[eid] = (tid, nxt)
        for lid, chain in chains.items():
            if lid not in names:
                continue
            pointed = {n for _, n in chain.values()}
            head = next((e for e in chain if e not in pointed), None)
            seen = set()
            while head in chain and head not in seen:
                seen.add(head)
                tid, head = chain[head]
                lists[names[lid]].append(tid)
    except sqlite3.Error as e:
        raise EngineError(f"it can't be read ({e})")
    finally:
        con.close()
    return tracks, lists
