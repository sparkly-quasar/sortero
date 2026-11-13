"""The Engine DJ library (m.db) Denon players read: schema, blobs, grids, cues, playlists.

The blobs are read back with decoders written here from libdjinterop's layouts
(src/djinterop/engine/v2/*_blob.cpp), not with enginelib's own code."""
import os, sqlite3, struct, unittest, zlib

from sortero import common
from sortero.gig import devicesql as D, enginelib as E
from sortero.gig.cues import Grid
from tests.test_tagio import TempDir

# libdjinterop's schema_3_0_2::verify_track: every Track column, sorted by name.
TRACK_COLUMNS = [
    "album", "albumArt", "albumArtId", "albumArtSourceHash", "artist", "bitrate", "bpm",
    "bpmAnalyzed", "comment", "composer", "dateAdded", "dateCreated", "explicitLyrics",
    "fileBytes", "fileType", "filename", "genre", "id", "isAnalyzed", "isAvailable",
    "isBeatGridLocked", "isMetadataImported", "isMetadataOfPackedTrackChanged",
    "isPerfomanceDataOfPackedTrackChanged", "isPlayed", "key", "label", "lastEditTime",
    "length", "originDatabaseUuid", "originTrackId", "path", "pdbImportKey", "playOrder",
    "playedIndicator", "rating", "remixer", "streamingFlags", "streamingSource",
    "timeLastPlayed", "title", "uri", "year"]


# ----------------------------------------------------- independent decoders
def unpack(blob):
    n = struct.unpack(">I", blob[:4])[0]
    data = zlib.decompress(blob[4:])
    assert len(data) == n
    return data


def read_track_data(blob):
    return struct.unpack(">dqiddd", unpack(blob))


def _read_grid(data, at):
    (n,) = struct.unpack_from(">q", data, at)          # count big-endian...
    at += 8
    out = []
    for _ in range(n):
        out.append(struct.unpack_from("<dqii", data, at))  # ...markers little-endian
        at += 24
    return out, at


def read_beat_data(blob):
    data = unpack(blob)
    rate, samples, is_set = struct.unpack_from(">ddB", data)
    default, at = _read_grid(data, 17)
    adjusted, at = _read_grid(data, at)
    return rate, samples, is_set, default, adjusted, data[at:]


def read_quick_cues(blob):
    data = unpack(blob)
    (n,) = struct.unpack_from(">q", data)
    at, pads = 8, []
    for _ in range(n):
        size = data[at]
        label = data[at + 1:at + 1 + size].decode("utf-8")
        at += 1 + size
        (offset,) = struct.unpack_from(">d", data, at)
        pads.append((label, offset, tuple(data[at + 8:at + 12])))
        at += 12
    adjusted, is_adjusted, default = struct.unpack_from(">dBd", data, at)
    assert at + 17 == len(data)
    return pads, adjusted, is_adjusted, default


def read_loops(blob):
    (n,) = struct.unpack_from("<q", blob)
    at, out = 8, []
    for _ in range(n):
        size = blob[at]
        label = blob[at + 1:at + 1 + size].decode("utf-8")
        at += 1 + size
        start, end, s, e = struct.unpack_from("<ddBB", blob, at)
        out.append((label, start, end, s, e, tuple(blob[at + 18:at + 22])))
        at += 22
    assert at == len(blob)
    return out


def read_overview(blob):
    data = unpack(blob)
    n, n2, per_point = struct.unpack_from(">qqd", data)
    assert n == n2
    points = [tuple(data[24 + 3 * i:27 + 3 * i]) for i in range(n)]
    loudest = tuple(data[24 + 3 * n:27 + 3 * n])
    assert len(data) == 27 + 3 * n
    return per_point, points, loudest


def performance(path, track_id=1):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT trackData, beatData, quickCues, loops, "
                           "overviewWaveFormData FROM PerformanceData WHERE trackId = ?",
                           (track_id,)).fetchone()
    finally:
        con.close()


def track(i, title, path, **kw):
    return D.Track(i, title, path, file_size=1000 + i, **kw)


# --------------------------------------------------------------------- tests
class Schema(TempDir):
    def build(self, tracks=(), lists=(), **kw):
        path = os.path.join(self.dir, "Engine Library", "Database2", "m.db")
        audio = {t.id: E.Audio(44100, 44100 * 60) for t in tracks}
        return path, E.build(path, list(tracks), list(lists), audio, **kw)

    def test_libdjinterops_3_0_2_schema(self):
        path, built = self.build()
        con = sqlite3.connect(path)
        kinds = {}
        for kind, name in con.execute("SELECT type, name FROM sqlite_master "
                                      "WHERE name NOT LIKE 'sqlite_%'"):
            kinds.setdefault(kind, set()).add(name)
        self.assertEqual(kinds["table"], {"Information", "AlbumArt", "Pack", "Track",
                                          "PerformanceData", "Playlist", "PlaylistEntity",
                                          "PreparelistEntity", "Smartlist"})
        self.assertEqual(kinds["view"], {"ChangeLog", "PlaylistAllParent",
                                         "PlaylistAllChildren", "PlaylistPath"})
        self.assertEqual(len(kinds["trigger"]), 16)
        self.assertEqual(len(kinds["index"]), 16)
        self.assertEqual(sorted(r[1] for r in con.execute("PRAGMA table_info(Track)")),
                         TRACK_COLUMNS)
        self.assertEqual(con.execute("SELECT schemaVersionMajor, schemaVersionMinor, "
                                     "schemaVersionPatch, uuid FROM Information").fetchall(),
                         [(3, 0, 2, built.uuid)])
        self.assertEqual(con.execute("SELECT id, hash, albumArt FROM AlbumArt").fetchall(),
                         [(1, "", None)])
        con.close()
        self.assertEqual(E.info(path), {"uuid": built.uuid, "schema": (3, 0, 2),
                                        "tracks": 0, "playlists": 0})

    def test_the_librarys_identity_is_kept_when_asked(self):
        _, built = self.build(library_uuid="1b4e28ba-2fa1-11d2-883f-0016d3cca427")
        self.assertEqual(built.uuid, "1b4e28ba-2fa1-11d2-883f-0016d3cca427")

    def test_tracks_point_at_the_audio_beside_the_engine_folder(self):
        # Sortero's ids needn't be ascending; Engine's must be (a trigger refuses reuse).
        tracks = [track(50, "Ünder", "/Contents/A/B/x.mp3", artist="A", key="8A", bpm=124.6,
                        duration=300, year=2024, bitrate=320, rating=4),
                  track(7, "Two", "/Contents/A/B/y.flac", key="F#m")]
        path, built = self.build(tracks)
        self.assertEqual(built.tracks, 2)
        con = sqlite3.connect(path)
        rows = con.execute("SELECT id, path, filename, fileType, title, key, bpm, bpmAnalyzed, "
                           "length, rating, fileBytes, originDatabaseUuid, originTrackId, "
                           "isAnalyzed, isAvailable, albumArtId FROM Track ORDER BY id").fetchall()
        con.close()
        self.assertEqual(rows[0], (1, "../Contents/A/B/x.mp3", "x.mp3", "mp3", "Ünder", 1, 124,
                                   124.6, 300, 80, 1050, built.uuid, 1, 1, 1, E.NO_ART))
        self.assertEqual(rows[1][:6], (2, "../Contents/A/B/y.flac", "y.flac", "flac", "Two", 7))
        tracks, _ = E.read(path)
        self.assertEqual(tracks, [(1, "/Contents/A/B/x.mp3", "Ünder", 1050),
                                  (2, "/Contents/A/B/y.flac", "Two", 1007)])

    def test_a_path_off_the_stick_reads_as_none(self):
        self.assertEqual(E.stick_path("../Contents/a.mp3"), "/Contents/a.mp3")
        self.assertEqual(E.stick_path("Music/a.mp3"), "/Engine Library/Music/a.mp3")
        self.assertIsNone(E.stick_path("../../Users/me/a.mp3"))
        self.assertIsNone(E.stick_path(""))

    def test_playlists_in_order_with_folders_and_repeats(self):
        tracks = [track(i, f"T{i}", f"/Contents/A/B/{i}.mp3") for i in (1, 2, 3)]
        lists = [D.Playlist(1, "Gig", 0, folder=True),
                 D.Playlist(2, "01 Warmup", 1, tracks=[3, 1, 3, 2]),
                 D.Playlist(3, "02 Peak", 1, tracks=[2]),
                 D.Playlist(4, "Gig", 0, tracks=[1]),               # a twin at the top
                 D.Playlist(5, "A;B", 0, tracks=[9])]               # 9 isn't on the stick
        path, built = self.build(tracks, lists)
        self.assertEqual(built.repeats, [("01 Warmup", "T3")])
        con = sqlite3.connect(path)
        rows = con.execute("SELECT id, title, parentListId, nextListId, isPersisted, "
                           "isExplicitlyExported FROM Playlist ORDER BY id").fetchall()
        paths = dict(con.execute("SELECT id, path FROM PlaylistPath"))
        con.close()
        self.assertEqual(rows, [(1, "Gig", 0, 4, 1, 1), (2, "01 Warmup", 1, 3, 1, 1),
                                (3, "02 Peak", 1, 0, 1, 1), (4, "Gig (2)", 0, 5, 1, 1),
                                (5, "A,B", 0, 0, 1, 1)])
        self.assertEqual(paths[3], "Gig;02 Peak;")          # Engine's own view agrees
        _, lists = E.read(path)
        self.assertEqual(lists, {"Gig": [], "01 Warmup": [3, 1, 2], "02 Peak": [2],
                                 "Gig (2)": [1], "A,B": []})

    def test_artwork_goes_in_albumart(self):
        tracks = [track(1, "a", "/Contents/a.mp3"), track(2, "b", "/Contents/b.mp3"),
                  track(3, "c", "/Contents/c.mp3")]
        tracks[0].artwork_id = tracks[1].artwork_id = 1
        path, _ = self.build(tracks, art={1: b"\xff\xd8jpeg"})
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("SELECT id, hash, albumArt FROM AlbumArt").fetchall(),
                         [(1, "", None), (2, E.art_hash(b"\xff\xd8jpeg"), b"\xff\xd8jpeg")])
        self.assertEqual(con.execute("SELECT albumArtId, albumArt FROM Track").fetchall(),
                         [(2, E.ART_URI), (2, E.ART_URI), (1, None)])
        con.close()
        self.assertEqual(E.art_hash(bytes(1)), "5ba93c9db0cff93f52b521d7420e43f6eda2784f")

    def test_all_or_nothing(self):
        path, built = self.build([track(1, "a", "/Contents/a.mp3")])
        with open(path, "rb") as fh:
            before = fh.read()
        with self.assertRaises(KeyError):                   # no Audio for the track
            E.build(path, [track(1, "a", "/Contents/a.mp3")], [], {})
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(os.listdir(os.path.dirname(path)), ["m.db"])

    def test_reading_something_else_is_an_error(self):
        p = self.path("m.db")
        with open(p, "wb") as fh:
            fh.write(b"x" * 4096)
        with self.assertRaises(E.EngineError):
            E.read(p)
        with self.assertRaises(E.EngineError):
            E.info(self.path("none.db"))


class Blobs(TempDir):
    def write(self, t, audio):
        path = self.path("m.db")
        E.build(path, [t], [], {t.id: audio})
        return performance(path)

    def test_every_blob_reads_back(self):
        cues = [D.Cue(1000, 0, None, "Drop ü"), D.Cue(500, -1), D.Cue(2000, -1, 4000, "Roll"),
                D.Cue(9000, 3, 9500)]
        columns = bytes((10, 20, 30, 127, 0, 64)) * 4500   # 60 s of pwv7 columns
        t = track(1, "a", "/Contents/a.mp3", key="Am", cues=cues)
        rate, samples = 44100, 44100 * 60
        ov = E.overview(rate, samples, columns)
        td, bd, qc, lp, ow = self.write(t, E.Audio(rate, samples, [Grid(0.1, 120.0)], ov))

        self.assertEqual(read_track_data(td), (44100.0, samples, 1, 0.0, 0.0, 0.0))

        r, s, is_set, default, adjusted, extra = read_beat_data(bd)
        self.assertEqual((r, s, is_set, extra), (44100.0, samples, 1, bytes(9)))
        self.assertEqual(default, adjusted)
        self.assertEqual(len(default), 2)

        pads, adjusted, is_adjusted, main = read_quick_cues(qc)
        self.assertEqual(len(pads), 8)
        self.assertEqual(pads[0], ("Drop ü", 44100.0, E.PAD_COLOURS[0]))   # hot cue A
        self.assertEqual(pads[1][:2], ("", 22050.0))       # the memory cue, on a free pad
        self.assertEqual(pads[2], ("", -1.0, (0, 0, 0, 0)))  # empty
        self.assertEqual(pads[3], ("", -1.0, (0, 0, 0, 0)))  # D was a loop: no pad
        self.assertEqual((adjusted, is_adjusted, main), (22050.0, 1, 22050.0))

        loops = read_loops(lp)
        self.assertEqual(len(loops), 8)
        self.assertEqual(loops[0][:5], ("", 396900.0, 418950.0, 1, 1))     # hot loop first
        self.assertEqual(loops[1][:5], ("Roll", 88200.0, 176400.0, 1, 1))
        self.assertEqual(loops[2], ("", -1.0, -1.0, 0, 0, (0, 0, 0, 0)))

        per_point, points, loudest = read_overview(ow)
        self.assertEqual(len(points), 1024)
        self.assertEqual(per_point, (samples // 420) * 420 / 1024)
        self.assertEqual(loudest, (254, 40, 128))
        self.assertEqual(points[0], (254, 40, 128))

    def test_no_waveform_no_grid_no_cues(self):
        t = track(1, "a", "/Contents/a.mp3")
        td, bd, qc, lp, ow = self.write(t, E.Audio(48000, 48000 * 10))
        self.assertEqual(read_track_data(td), (48000.0, 480000, 0, 0.0, 0.0, 0.0))
        self.assertEqual(read_beat_data(bd)[2:5], (0, [], []))
        pads, _, _, main = read_quick_cues(qc)
        self.assertEqual((pads, main), ([("", -1.0, (0, 0, 0, 0))] * 8, 0.0))
        self.assertEqual(read_overview(ow), (0.0, [], (0, 0, 0)))

    def test_a_long_label_is_cut_on_a_character(self):
        pads, _, _, _ = E.pads_and_loops([D.Cue(0, 0, None, "é" * 200)], 44100)
        blob = E.quick_cues(pads, 0.0)
        self.assertEqual(read_quick_cues(blob)[0][0][0], "é" * 127)


class Grids(unittest.TestCase):
    def test_one_tempo_is_two_markers_from_beat_minus_4_to_past_the_end(self):
        rate, period = 44100, 0.5                         # 120 BPM
        m = E.beat_grid([Grid(0.1, 120.0)], rate, rate * 60)
        self.assertEqual([x.index for x in m], [-4, 120])
        self.assertAlmostEqual(m[0].samples, (0.1 - 4 * period) * rate)
        self.assertAlmostEqual(m[1].samples, (0.1 + 120 * period) * rate)   # 60.1 s
        data = E._grid(m)
        self.assertEqual(struct.unpack_from("<dqii", data, 8)[1:], (-4, 124, 0))

    def test_downbeats_land_on_multiples_of_four(self):
        # The first beat is the third of its bar, so the bar began two beats earlier.
        m = E.beat_grid([Grid(1.0, 120.0, beat=3)], 1000, 10_000)
        self.assertEqual(m[0].index, -4)
        self.assertAlmostEqual(m[0].samples, (1.0 - 6 * 0.5) * 1000)
        self.assertEqual(m[1].index, 20)                 # 18 beats to the end, from index 2
        self.assertAlmostEqual(m[1].samples, (1.0 + 18 * 0.5) * 1000)

    def test_a_tempo_change_is_a_marker(self):
        m = E.beat_grid([Grid(0.0, 120.0), Grid(10.0, 100.0)], 1000, 20_000)
        self.assertEqual([(x.index, round(x.samples)) for x in m],
                         [(-4, -2000), (20, 10_000), (37, 20_200)])

    def test_nothing_to_go_on(self):
        self.assertEqual(E.beat_grid([], 44100, 44100), [])
        self.assertEqual(E.beat_grid([Grid(0.0, 0.0)], 44100, 44100), [])
        self.assertEqual(E.beat_grid([Grid(5.0, 120.0)], 44100, 44100), [])   # past the end


class Cues(unittest.TestCase):
    def test_hot_cues_keep_their_pads_and_memory_cues_fill_the_rest(self):
        cues = [D.Cue(ms, hot) for ms, hot in ((100, 2), (50, -1), (10, -1), (900, 7))]
        pads, loops, main, dropped = E.pads_and_loops(cues, 1000)
        self.assertEqual([p.samples for p in pads],
                         [10.0, 50.0, 100.0, -1, -1, -1, -1, 900.0])
        self.assertEqual((main, dropped), (10.0, 0))

    def test_what_doesnt_fit_is_counted(self):
        cues = [D.Cue(i, -1) for i in range(10)] + \
               [D.Cue(i, -1, i + 1) for i in range(9)]
        pads, loops, _, dropped = E.pads_and_loops(cues, 1000)
        self.assertEqual(sum(p.samples >= 0 for p in pads), 8)
        self.assertEqual(sum(s.start >= 0 for s in loops), 8)
        self.assertEqual(dropped, 3)


class Keys(unittest.TestCase):
    def test_libdjinterops_numbers(self):
        for text, n in (("C", 0), ("Am", 1), ("G", 2), ("Em", 3), ("F#m", 7), ("Gb", 12),
                        ("C#m", 9), ("Dbm", 9), ("Ebm", 13), ("Bbm", 15), ("Ab", 16),
                        ("Cm", 19), ("F", 22), ("Dm", 23), ("A minor", 1), ("Bmin", 5),
                        ("Emaj", 8), ("8A", 1), ("8B", 0), ("12B", 8), ("1A", 11),
                        ("01A", 11), ("1m", 1), ("1d", 0), ("6d", 10), ("F♯m", 7)):
            self.assertEqual(E.key(text), n, text)

    def test_camelot_and_musical_agree(self):
        for musical, camelot in common.CAMELOT.items():
            self.assertEqual(E.key(musical), E.key(camelot), musical)

    def test_not_a_key(self):
        for text in ("", None, "H", "13A", "Am7", "unknown"):
            self.assertIsNone(E.key(text), text)


if __name__ == "__main__":
    unittest.main()
