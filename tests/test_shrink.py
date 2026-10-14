"""Shrinking the library: lossless tracks become MP3 320, tags kept, originals archived."""
import os, sqlite3, unittest
from unittest import mock

from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3

from sortero import journal, mixxx, playlists, shrink as S
from sortero.library import Rec
from sortero.gig.probe import probe
from tests.test_convert import CUES, needs_ffmpeg, image, tone
from tests.test_tagio import TempDir


def rec(root, path, protected=False):
    return Rec(path=path, rel=os.path.relpath(path, root), protected=protected)


class Archive(TempDir):
    def test_outside_the_collection(self):
        root = self.path("Music")
        self.assertEqual(S.archive_dir(root), root + " - Originals")
        for bad in (os.path.join(root, "Originals"), self.dir):
            with self.subTest(bad=bad), self.assertRaises(S.ArchiveError):
                S.archive_dir(root, bad)


@needs_ffmpeg
class Shrink(TempDir):
    def setUp(self):
        super().setUp()
        self.root = self.path("Music")
        self.archive = S.archive_dir(self.root)
        os.makedirs(os.path.join(self.root, "Techno"))

    def at(self, name):
        return os.path.join(self.root, "Techno", name)

    def flac(self, name="Artist - Title.flac", rate=96000):
        p = tone(self.at(name), rate, ("-c:a", "flac", "-sample_fmt", "s32"), seconds=2)
        f = FLAC(p)
        f["ARTIST"], f["TITLE"], f["INITIALKEY"], f["BPM"] = "Artist", "Title", "8A", "126"
        f["COMMENT"] = "8A - Energy 6"
        f["CUEPOINTS"] = CUES       # how Mixed In Key's cue points sit in a FLAC
        pic = Picture()
        pic.type, pic.mime = 3, "image/png"
        pic.data = image(self.path("art.png"), 1400)
        f.add_picture(pic)
        f.save()
        return p

    def archives(self):
        return [a for a in S.archives() if a.folder == self.archive]

    def plan(self, *paths, **kw):
        return S.plan(self.root, [rec(self.root, p, **kw) for p in paths], self.archive)

    def test_only_lossless_tracks_are_planned(self):
        files = [self.flac(), tone(self.at("w.wav"), 44100, ("-c:a", "pcm_s24le")),
                 tone(self.at("a.aiff"), 48000, ("-c:a", "pcm_s16be")),
                 tone(self.at("l.m4a"), 44100, ("-c:a", "alac")),
                 tone(self.at("c.m4a"), 44100, ("-c:a", "aac", "-b:a", "256k")),
                 tone(self.at("m.mp3"), 44100, ("-c:a", "libmp3lame", "-b:a", "192k"))]
        items = self.plan(*files)
        self.assertEqual(sorted(os.path.basename(it.src) for it in items),
                         ["Artist - Title.flac", "a.aiff", "l.m4a", "w.wav"])
        self.assertTrue(all(it.status == S.CONVERT for it in items))
        it = next(i for i in items if i.src.endswith(".flac"))
        self.assertEqual(it.mp3, self.at("Artist - Title.mp3"))
        self.assertEqual(it.archive, os.path.join(self.archive, "Techno", "Artist - Title.flac"))
        self.assertAlmostEqual(it.estimate, 2 * 40000, delta=4000)
        n, now, after = S.totals(items)
        self.assertEqual(n, 4)
        self.assertGreater(now, after)

    def test_protected_tracks_and_name_clashes_are_left_alone(self):
        self.assertEqual(self.plan(self.flac(), protected=True), [])
        tone(self.at("Artist - Title.mp3"), 44100, ("-c:a", "libmp3lame"))
        [it] = self.plan(self.at("Artist - Title.flac"))
        self.assertEqual(it.status, S.SKIP)
        self.assertIn("already beside it", it.why)

    def test_converts_keeps_every_tag_and_archives_the_original(self):
        src = self.flac()
        art = FLAC(src).pictures[0].data
        playlists.write(self.root, "Peak", [src])
        res = S.run(self.root, self.plan(src), self.archive, workers=1)
        [it] = res.done
        mp3 = self.at("Artist - Title.mp3")
        self.assertFalse(os.path.exists(src))
        self.assertTrue(os.path.isfile(os.path.join(self.archive, "Techno",
                                                    "Artist - Title.flac")))
        info = probe(mp3)
        self.assertEqual((info.format, info.rate), ("mp3", 44100))
        self.assertAlmostEqual(info.extras["duration"], 2.0, delta=S.LENGTH_SLACK)
        tags = ID3(mp3)
        self.assertEqual(str(tags["TPE1"]), "Artist")
        self.assertEqual(str(tags["TKEY"]), "8A")
        self.assertEqual(str(tags["TBPM"]), "126")
        self.assertIn("GEOB:CuePoints", tags)                 # Mixed In Key's cues
        self.assertEqual(tags.getall("APIC")[0].data, art)    # artwork untouched
        self.assertEqual(playlists.entries(self.root, "Peak"), [mp3])
        self.assertEqual(res.playlists, 1)
        self.assertGreater(res.saved, 0)
        # one History entry undoes the lot
        journal.revert(res.journal, log=lambda m: None)
        self.assertTrue(os.path.isfile(src))
        self.assertFalse(os.path.exists(mp3))
        self.assertEqual(playlists.entries(self.root, "Peak"), [src])

    def test_mixxx_follows_and_undo_puts_it_back(self):
        src = self.flac()
        db = self.path("mixxxdb.sqlite")
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE track_locations (id INTEGER PRIMARY KEY, location TEXT UNIQUE,
                filename TEXT, directory TEXT, filesize INTEGER);
            CREATE TABLE library (id INTEGER PRIMARY KEY, location INTEGER, samplerate INTEGER,
                bitrate INTEGER, bpm REAL, beats_version TEXT, beats BLOB);
            CREATE TABLE cues (id INTEGER PRIMARY KEY, track_id INTEGER, type INTEGER,
                position REAL, length REAL, hotcue INTEGER);""")
        con.execute("INSERT INTO track_locations VALUES (1, ?, 'x', 'y', 1)", (src,))
        con.execute("INSERT INTO library VALUES (1, 1, 96000, 4608, 126.0, NULL, NULL)")
        con.execute("INSERT INTO cues (track_id, type, position, length, hotcue) "
                    "VALUES (1, 1, 1920000, 0, 0)")
        con.commit()
        con.close()

        def where():
            c = sqlite3.connect(db)
            try:
                return (c.execute("SELECT location FROM track_locations").fetchone()[0],
                        c.execute("SELECT position FROM cues").fetchone()[0])
            finally:
                c.close()
        with mock.patch.object(mixxx, "running", return_value=False), \
                mock.patch.object(mixxx, "backup"):
            res = S.run(self.root, self.plan(src), self.archive, mixxx_db=db, workers=1)
            self.assertEqual(res.mixxx, 1)
            self.assertEqual(where(), (self.at("Artist - Title.mp3"), 1920000 * 44100 / 96000))
            journal.revert(res.journal, log=lambda m: None)
        self.assertEqual(where(), (src, 1920000))
        self.assertTrue(os.path.isfile(src))

    def test_an_mp3_that_fails_its_check_changes_nothing(self):
        src = self.flac()
        with mock.patch.object(S, "check", return_value="the MP3 is 1.0 s long"):
            res = S.run(self.root, self.plan(src), self.archive, workers=1)
        [it] = res.failed
        self.assertIn("1.0 s", it.why)
        self.assertTrue(os.path.isfile(src))
        self.assertEqual(sorted(os.listdir(self.at(""))), ["Artist - Title.flac"])
        self.assertIsNone(res.journal)          # nothing changed, nothing to undo

    def test_delete_the_archive_then_it_cant_be_undone(self):
        src = self.flac()
        res = S.run(self.root, self.plan(src), self.archive, workers=1)
        [arc] = self.archives()
        self.assertEqual(len(arc.files), 1)
        self.assertGreater(arc.bytes, 0)
        deleted, kept, freed = S.delete_archive(arc, log=lambda m: None)
        self.assertEqual((deleted, kept), (1, 0))
        self.assertFalse(os.path.exists(self.archive))
        self.assertEqual(self.archives(), [])
        said = []
        self.assertEqual(journal.revert(res.journal, log=said.append), (0, 0))
        self.assertIn("can't be undone", said[0])
        self.assertTrue(os.path.isfile(self.at("Artist - Title.mp3")))   # MP3 stays

    def test_an_original_whose_mp3_has_gone_is_kept(self):
        src = self.flac()
        S.run(self.root, self.plan(src), self.archive, workers=1)
        os.remove(self.at("Artist - Title.mp3"))
        [arc] = self.archives()
        deleted, kept, _ = S.delete_archive(arc, log=lambda m: None)
        self.assertEqual((deleted, kept), (0, 1))
        self.assertTrue(os.path.isfile(arc.files[0][0]))


class Mixxx(TempDir):
    def setUp(self):
        super().setUp()
        self.db = self.path("mixxxdb.sqlite")
        con = sqlite3.connect(self.db)
        con.executescript("""
            CREATE TABLE track_locations (id INTEGER PRIMARY KEY, location TEXT UNIQUE,
                filename TEXT, directory TEXT, filesize INTEGER);
            CREATE TABLE library (id INTEGER PRIMARY KEY, location INTEGER, samplerate INTEGER,
                bitrate INTEGER, bpm REAL, beats_version TEXT, beats BLOB);
            CREATE TABLE cues (id INTEGER PRIMARY KEY, track_id INTEGER, type INTEGER,
                position REAL, length REAL, hotcue INTEGER);""")
        con.execute("INSERT INTO track_locations VALUES (1, '/m/a.flac', 'a.flac', '/m', 9)")
        con.execute("INSERT INTO library VALUES (7, 1, 96000, 4608, 126.0, ?, ?)",
                    (mixxx.GRID, mixxx.grid_blob(b"", 126.0, 48000)))
        con.executemany("INSERT INTO cues (track_id, type, position, length, hotcue) "
                        "VALUES (7, ?, ?, ?, ?)",
                        [(1, 2 * 96000 * 10, 0, 0), (4, 2 * 96000 * 20, 2 * 96000 * 4, 1),
                         (2, -1, 0, -1)])
        con.commit()
        con.close()
        p = mock.patch.object(mixxx, "running", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def read(self):
        con = sqlite3.connect(self.db)
        try:
            loc = con.execute("SELECT location, filename FROM track_locations").fetchone()
            lib = con.execute("SELECT samplerate, bitrate, beats FROM library").fetchone()
            cues = con.execute("SELECT position, length FROM cues ORDER BY id").fetchall()
        finally:
            con.close()
        return loc, lib, cues

    def test_relink_rescales_cues_and_grid_to_the_new_rate(self):
        changes, notes = mixxx.relink(self.db, [("/m/a.flac", "/m/a.mp3", 44100)])
        self.assertEqual((len(changes), notes), (1, []))
        loc, lib, cues = self.read()
        self.assertEqual(loc, ("/m/a.mp3", "a.mp3"))
        self.assertEqual(lib[:2], (44100, 320))
        self.assertEqual(mixxx.grid_first_beat(lib[2]), 22050)
        self.assertEqual(cues, [(2 * 44100 * 10, 0), (2 * 44100 * 20, 2 * 44100 * 4),
                                (-1, 0)])
        mixxx.unrelink(self.db, {"db": self.db, **changes[0]})
        loc, lib, cues = self.read()
        self.assertEqual(loc, ("/m/a.flac", "a.flac"))
        self.assertEqual(lib[:2], (96000, 4608))
        self.assertEqual(mixxx.grid_first_beat(lib[2]), 48000)
        self.assertEqual(cues[0], (2 * 96000 * 10, 0))

    def test_a_beat_map_is_left_where_it_was(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE library SET beats_version = 'BeatMap-1.0'")
        con.commit()
        con.close()
        changes, notes = mixxx.relink(self.db, [("/m/a.flac", "/m/a.mp3", 44100)])
        self.assertEqual(changes, [])
        self.assertIn("beat map", notes[0])
        self.assertEqual(self.read()[0][0], "/m/a.flac")

    def test_a_track_mixxx_doesnt_know_is_skipped(self):
        self.assertEqual(mixxx.relink(self.db, [("/m/b.flac", "/m/b.mp3", 44100)]), ([], []))

    def test_mixxx_must_be_closed(self):
        with mock.patch.object(mixxx, "running", return_value=True), \
                self.assertRaises(mixxx.MixxxOpen):
            mixxx.relink(self.db, [("/m/a.flac", "/m/a.mp3", 44100)])


if __name__ == "__main__":
    unittest.main()
