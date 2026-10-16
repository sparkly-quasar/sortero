"""Write and read exportLibrary.db, the OneLibrary database the newest Pioneer players read.

The CDJ-3000X, XDJ-AZ, OPUS-QUAD and OMNIS-DUO read this instead of
export.pdb (AlphaTheta's USB notice, see profiles.LIBRARY_NOTICE), and
rekordbox 7.2.11+ writes both to every stick. It sits beside export.pdb in
PIONEER/rekordbox/ and points at the same audio and the same ANLZ files.

It's an SQLite database encrypted with SQLCipher 4. The passphrase is the same
on every stick (it has to be, or no other computer could read an export), and
rekordbox carries it obfuscated: base85, an XOR, zlib. It's already public,
in pyrekordbox (MIT) and in rbxport (GPL-2.0-or-later), whose builder this
follows (crates/rbl-onelibrary/src/build.rs): the schema, the browse menus
and the colour names are transcribed from a real rekordbox export there.

The passphrase is used for interoperability only: to write a user's own
library to their own stick, and to read sticks rekordbox or Sortero wrote. It
lives in this one place (PASSPHRASE_BLOB), and everything here is skipped if
the SQLCipher module isn't installed (available()).
"""
import base64, os, zlib

PASSPHRASE_BLOB = (b"PN_1dH8$oLJY)16j_RvM6qphWw`476>;C1cWmI#se(PG`j}~xAjlufj?`#0i{;"
                   b"=glh(SkW)y0>n?YEiD`l%t(")
XOR_KEY = b"657f48f84c437cc1"
PASSPHRASE_PREFIX = "r8gd"
FILE_NAME = "exportLibrary.db"
DB_VERSION = "1000"


class OneLibraryError(Exception):
    pass


def _sqlcipher():
    try:
        from sqlcipher3 import dbapi2
        return dbapi2
    except ImportError:
        return None


def available():
    return _sqlcipher() is not None


def passphrase():
    data = base64.b85decode(PASSPHRASE_BLOB)
    key = zlib.decompress(bytes(b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(data)))
    key = key.decode("ascii")
    if not key.startswith(PASSPHRASE_PREFIX):
        raise OneLibraryError("the OneLibrary passphrase didn't come out right")
    return key


def _open(path):
    sql = _sqlcipher()
    if sql is None:
        raise OneLibraryError("Sortero was built without SQLCipher, so it can't read or "
                              "write OneLibrary")
    con = sql.connect(path)
    # The key before anything else, or the file is read (or made) as plain SQLite.
    con.execute(f"PRAGMA key = '{passphrase()}'")
    return con


# ------------------------------------------------------------------ schema
SCHEMA = (
    "CREATE TABLE album(album_id integer primary key, name varchar, artist_id integer, image_id integer, isComplation integer, nameForSearch varchar)",
    "CREATE TABLE artist(artist_id integer primary key, name varchar, nameForSearch varchar)",
    "CREATE TABLE category(category_id integer primary key, menuItem_id integer, sequenceNo integer, isVisible integer)",
    "CREATE TABLE color(color_id integer primary key, name varchar)",
    "CREATE TABLE content(content_id integer primary key, title varchar, titleForSearch varchar, subtitle varchar, bpmx100 integer, length integer, trackNo integer, discNo integer, artist_id_artist integer, artist_id_remixer integer, artist_id_originalArtist integer, artist_id_composer integer, artist_id_lyricist integer, album_id integer, genre_id integer, label_id integer, key_id integer, color_id integer, image_id integer, djComment varchar, rating integer, releaseYear integer, releaseDate varchar, dateCreated varchar, dateAdded varchar, path varchar, fileName varchar, fileSize integer, fileType integer, bitrate integer, bitDepth integer, samplingRate integer, isrc varchar, djPlayCount integer, isHotCueAutoLoadOn integer, isKuvoDeliverStatusOn integer, kuvoDeliveryComment varchar, masterDbId integer, masterContentId integer, analysisDataFilePath varchar, analysedBits integer, contentLink integer, hasModified integer, cueUpdateCount integer, analysisDataUpdateCount integer, informationUpdateCount integer)",
    "CREATE TABLE cue(cue_id integer primary key, content_id integer, kind integer, colorTableIndex integer, cueComment varchar, isActiveLoop integer, beatLoopNumerator integer, beatLoopDenominator integer, inUsec integer, outUsec integer, in150FramePerSec integer, out150FramePerSec integer, inMpegFrameNumber integer, outMpegFrameNumber integer, inMpegAbs integer, outMpegAbs integer, inDecodingStartFramePosition integer, outDecodingStartFramePosition integer, inFileOffsetInBlock integer, OutFileOffsetInBlock integer, inNumberOfSampleInBlock integer, outNumberOfSampleInBlock integer)",
    "CREATE TABLE genre(genre_id integer primary key, name varchar)",
    "CREATE TABLE history(history_id integer primary key, sequenceNo integer, name varchar, attribute integer, history_id_parent integer)",
    "CREATE TABLE history_content(history_id integer, content_id integer, sequenceNo integer)",
    "CREATE TABLE hotCueBankList(hotCueBankList_id integer primary key, sequenceNo integer, name varchar, image_id integer, attribute integer, hotCueBankList_id_parent integer)",
    "CREATE TABLE hotCueBankList_cue(hotCueBankList_id integer, cue_id integer, sequenceNo integer)",
    "CREATE TABLE image(image_id integer primary key, path varchar)",
    "CREATE TABLE key(key_id integer primary key, name varchar)",
    "CREATE TABLE label(label_id integer primary key, name varchar)",
    "CREATE TABLE menuItem(menuItem_id integer primary key, kind integer, name varchar)",
    "CREATE TABLE myTag(myTag_id integer primary key, sequenceNo integer, name varchar, attribute integer, myTag_id_parent integer)",
    "CREATE TABLE myTag_content(myTag_id integer, content_id integer)",
    "CREATE TABLE playlist(playlist_id integer primary key, sequenceNo integer, name varchar, image_id integer, attribute integer, playlist_id_parent integer)",
    "CREATE TABLE playlist_content(playlist_id integer, content_id integer, sequenceNo integer)",
    "CREATE TABLE property(deviceName varchar, dbVersion varchar, numberOfContents integer, createdDate varchar, backGroundColorType integer, myTagMasterDBID integer)",
    "CREATE TABLE recommendedLike(content_id_1 integer, content_id_2 integer, rating integer, createdDate integer)",
    "CREATE TABLE sort(sort_id integer primary key, menuItem_id integer, sequenceNo integer, isVisible integer, isSelectedAsSubColumn integer)",
)

# The browse menus. A player translates a name wrapped in U+FFFA/U+FFFB, so
# it's written that way, as rekordbox does.
MENU_ITEMS = ((1, 128, "GENRE"), (2, 129, "ARTIST"), (3, 130, "ALBUM"), (4, 131, "TRACK"),
              (5, 133, "BPM"), (6, 134, "RATING"), (7, 135, "YEAR"), (8, 136, "REMIXER"),
              (9, 137, "LABEL"), (10, 138, "ORIGINAL ARTIST"), (11, 139, "KEY"),
              (12, 141, "CUE"), (13, 142, "COLOR"), (14, 146, "TIME"), (15, 147, "BITRATE"),
              (16, 148, "FILE NAME"), (17, 132, "PLAYLIST"), (18, 152, "HOT CUE BANK"),
              (19, 149, "HISTORY"), (20, 145, "SEARCH"), (21, 150, "COMMENTS"),
              (22, 140, "DATE ADDED"), (23, 151, "DJ PLAY COUNT"), (24, 144, "FOLDER"),
              (25, 161, "DEFAULT"), (26, 162, "ALPHABET"), (27, 170, "MATCHING"))
# (category_id, menuItem_id, sequenceNo, isVisible)
CATEGORIES = ((1, 1, 0, 0), (2, 2, 1, 1), (3, 3, 2, 1), (4, 4, 3, 1), (5, 17, 5, 1),
              (6, 5, 0, 0), (7, 6, 0, 0), (8, 7, 0, 0), (9, 8, 0, 0), (10, 9, 0, 0),
              (11, 10, 0, 0), (12, 11, 4, 1), (15, 13, 0, 0), (17, 24, 9, 1), (18, 20, 7, 1),
              (19, 14, 0, 0), (20, 15, 0, 0), (21, 16, 0, 0), (22, 19, 6, 1), (23, 18, 0, 0),
              (26, 27, 8, 1), (27, 22, 10, 1))
# (sort_id, menuItem_id, sequenceNo, isVisible, isSelectedAsSubColumn)
SORTS = ((0, 25, 1, 1, 0), (1, 26, 2, 1, 0), (2, 2, 3, 1, 0), (3, 3, 4, 1, 0),
         (4, 5, 5, 1, 0), (5, 6, 6, 1, 0), (6, 1, 0, 0, 0), (7, 21, 0, 0, 1),
         (8, 14, 0, 0, 0), (9, 8, 0, 0, 0), (10, 9, 0, 0, 0), (11, 10, 0, 0, 0),
         (12, 11, 7, 1, 0), (13, 15, 0, 0, 0), (15, 13, 0, 0, 0), (16, 23, 0, 0, 0),
         (17, 22, 0, 0, 0))
COLORS = ("Pink", "Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple")
FILE_TYPES = {".mp3": 1, ".m4a": 4, ".mp4": 4, ".aac": 4, ".flac": 5, ".wav": 11,
              ".aif": 12, ".aiff": 12}
# rekordbox's Analysed value for an analysed track is 105; an export keeps it
# without bit 64, as rbxport found.
ANALYSED = 105 & ~64


def cue_kind(hot):
    """OneLibrary's cue kind: memory 0, hot cues A-C 1-3, D onwards from 5."""
    return 0 if hot < 0 else hot + 1 if hot < 3 else hot + 2


# ------------------------------------------------------------------- write
def build(path, tracks, playlists, device_name="", today=""):
    """Write exportLibrary.db at `path` (devicesql.Track and Playlist lists), all or nothing."""
    import time
    today = today or time.strftime("%Y-%m-%d")
    tmp = path + ".sortero-tmp"
    for p in (tmp, tmp + "-journal"):
        if os.path.exists(p):
            os.remove(p)
    con = _open(tmp)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        for statement in SCHEMA:
            con.execute(statement)
        con.executemany("INSERT INTO menuItem VALUES (?, ?, ?)",
                        [(i, k, f"￺{n}￻") for i, k, n in MENU_ITEMS])
        con.executemany("INSERT INTO category VALUES (?, ?, ?, ?)", CATEGORIES)
        con.executemany("INSERT INTO sort VALUES (?, ?, ?, ?, ?)", SORTS)
        con.executemany("INSERT INTO color VALUES (?, ?)",
                        [(i + 1, n) for i, n in enumerate(COLORS)])
        lookups = {"artist": {}, "album": {}, "genre": {}, "key": {}}

        def intern(table, name, artist=0):
            if not name:
                return 0
            ids = lookups[table]
            if name not in ids:
                ids[name] = len(ids) + 1
                if table == "artist":
                    con.execute("INSERT INTO artist VALUES (?, ?, ?)", (ids[name], name, name))
                elif table == "album":
                    con.execute("INSERT INTO album (album_id, name, artist_id) VALUES (?, ?, ?)",
                                (ids[name], name, artist))
                else:
                    con.execute(f"INSERT INTO {table} VALUES (?, ?)", (ids[name], name))
            return ids[name]

        from .devicesql import color_id
        for t in tracks:
            artist = intern("artist", t.artist)
            tempo = t.beats[0].tempo if t.beats else int(round(t.bpm * 100))
            name = t.file_path.rsplit("/", 1)[-1]
            con.execute(
                "INSERT INTO content (content_id, title, titleForSearch, bpmx100, length, "
                "trackNo, artist_id_artist, album_id, genre_id, label_id, key_id, color_id, "
                "djComment, rating, releaseYear, dateCreated, dateAdded, path, fileName, "
                "fileSize, fileType, bitrate, bitDepth, samplingRate, djPlayCount, "
                "isHotCueAutoLoadOn, masterDbId, masterContentId, analysisDataFilePath, "
                "analysedBits, hasModified) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 16, "
                "?, 0, 1, 0, 0, ?, ?, 0)",
                (t.id, t.title, t.title, tempo, t.duration, t.track_number, artist,
                 intern("album", t.album, artist), intern("genre", t.genre),
                 intern("key", t.key), color_id(t.color), t.comment, t.rating, t.year,
                 today, today, t.file_path, name, t.file_size,
                 FILE_TYPES.get(os.path.splitext(name)[1].lower(), 0), t.bitrate,
                 t.sample_rate, t.analyze_path, ANALYSED if t.beats else 0))
            for c in t.cues:
                loop = c.loop_end_ms is not None
                con.execute(
                    "INSERT INTO cue (content_id, kind, colorTableIndex, cueComment, "
                    "isActiveLoop, beatLoopNumerator, beatLoopDenominator, inUsec, outUsec, "
                    "in150FramePerSec, out150FramePerSec) VALUES (?, ?, 0, ?, 0, 0, 0, ?, ?, ?, ?)",
                    (t.id, cue_kind(c.hot), c.name, c.ms * 1000,
                     c.loop_end_ms * 1000 if loop else -1, c.ms * 150 // 1000,
                     c.loop_end_ms * 150 // 1000 if loop else -1))
        siblings = {}
        for p in playlists:
            siblings[p.parent] = siblings.get(p.parent, 0) + 1
            con.execute("INSERT INTO playlist VALUES (?, ?, ?, NULL, ?, ?)",
                        (p.id, siblings[p.parent], p.name, 1 if p.folder else 0, p.parent))
            con.executemany("INSERT INTO playlist_content VALUES (?, ?, ?)",
                            [(p.id, tid, i) for i, tid in enumerate(p.tracks, 1)])
        con.execute("INSERT INTO property VALUES (?, ?, ?, ?, 0, 0)",
                    (device_name, DB_VERSION, len(tracks), today))
        con.commit()
    finally:
        con.close()
    with open(tmp, "rb+") as fh:
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


# -------------------------------------------------------------------- read
def read(path):
    """[(content_id, path on the stick, title, file size)] and {playlist name: [content_id]}."""
    con = _open(path)
    try:
        tracks = con.execute("SELECT content_id, path, title, fileSize FROM content "
                             "ORDER BY content_id").fetchall()
        names = dict(con.execute("SELECT playlist_id, name FROM playlist WHERE attribute = 0"))
        lists = {n: [] for n in names.values()}
        for pid, cid in con.execute("SELECT playlist_id, content_id FROM playlist_content "
                                    "ORDER BY playlist_id, sequenceNo"):
            if pid in names:
                lists[names[pid]].append(cid)
    except Exception as e:
        raise OneLibraryError(f"it can't be read ({e})")
    finally:
        con.close()
    return tracks, lists
