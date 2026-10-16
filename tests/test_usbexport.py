"""Writing a rekordbox Device Library to a stick without rekordbox, read back by
independent parsers: crate-digger's for export.pdb (the one the stick check
uses), and pyrekordbox's for the analysis files when it's installed."""
import json, os, struct, unittest

from sortero.gig import anlz, cues, devicesql as D, pdb, profiles as P, usbexport as U
from sortero.gig import rbxml, verify as V
from tests.audio import write_wav
from tests.test_tagio import TempDir

try:
    from pyrekordbox.anlz import AnlzFile
except ImportError:
    AnlzFile = None

NXS2 = P.get("cdj-2000nxs2")


class Strings(unittest.TestCase):
    def test_the_three_kinds(self):
        self.assertEqual(D.string("Am"), bytes([7]) + b"Am")
        self.assertEqual(D.string(""), b"\x03")
        long = "x" * 200
        self.assertEqual(D.string(long)[:4], struct.pack("<BHB", 0x40, 204, 0))
        self.assertEqual(D.string("Café"), struct.pack("<BHB", 0x90, 12, 0)
                         + "Café".encode("utf-16-le"))

    def test_index_size(self):
        self.assertEqual(D.index_bytes(11), 26)     # measured on a real page
        self.assertEqual(D.index_bytes(16), 36)
        self.assertEqual(D.index_bytes(17), 42)

    def test_colours(self):
        self.assertEqual(D.color_id(None), 0)
        self.assertEqual(D.color_id((0xF8, 0x70, 0x90)), 2)      # Red
        self.assertEqual(D.color_id((0, 0x40, 0xFF)), 7)         # nearest is Blue


class Database(TempDir):
    def test_hundreds_of_tracks_over_many_pages_read_back(self):
        tracks = [D.Track(i, f"Track {i}" + (" é" if i % 7 == 0 else ""),
                          f"/Contents/A{i % 5}/Album/{i:03d}.mp3", U.anlz_path(i),
                          artist=f"A{i % 5}", album="Album", genre="Techno", key="Am",
                          file_size=10_000_000 + i, bpm=128)
                  for i in range(1, 401)]
        lists = [D.Playlist(1, "Gig", folder=True),
                 D.Playlist(2, "01 Warmup", 1, tracks=list(range(1, 201))),
                 D.Playlist(3, "02 Peak", 1, tracks=list(range(400, 200, -1)))]
        p = self.path("export.pdb")
        with open(p, "wb") as fh:
            fh.write(D.build(tracks, lists))
        e = pdb.read(p)
        self.assertEqual([t.id for t in e.tracks], list(range(1, 401)))
        self.assertEqual(e.tracks[6].title, "Track 7 é")
        self.assertEqual(e.tracks[399].size, 10_000_400)
        self.assertEqual(e.playlists["02 Peak"][:2], [400, 399])
        self.assertEqual(len(e.playlists["01 Warmup"]), 200)


class LikeRekordbox(TempDir):
    """The page structure of tests/fixtures' real rekordbox export, table by table."""

    def layout(self, path):
        from kaitaistruct import KaitaiStream
        from sortero.vendor.rekordbox_pdb import RekordboxPdb
        with open(path, "rb") as fh:
            data = fh.read()
        db = RekordboxPdb(False, KaitaiStream(__import__("io").BytesIO(data)))
        out = []
        for t in db.tables:
            first = data[t.first_page.index * D.PAGE:][:D.PAGE]
            last = data[t.last_page.index * D.PAGE:][:D.PAGE]
            out.append((first[0x1B], struct.unpack_from("<I", last, 0x0C)[0]
                        == t.empty_candidate or t.first_page.index == t.last_page.index))
        return out, db

    def test_index_pages_and_chain_ends(self):
        real, _ = self.layout(os.path.join(os.path.dirname(__file__), "fixtures",
                                           "rekordcrate-demo", "export.pdb"))
        p = self.path("export.pdb")
        t = D.Track(1, "T", "/Contents/A/B/t.mp3", U.anlz_path(1), artist="A", genre="G")
        with open(p, "wb") as fh:
            fh.write(D.build([t], [D.Playlist(1, "P", tracks=[1])]))
        ours, db = self.layout(p)
        self.assertEqual({f for f, _ in real}, {D.INDEX_PAGE_FLAGS})
        self.assertEqual([f for f, _ in ours], [D.INDEX_PAGE_FLAGS] * 20)
        self.assertTrue(all(ok for _, ok in ours))
        row = next(pdb._rows(db, type(db).PageType.tracks))
        self.assertEqual(row.autoload_hot_cues.body.text, "ON")

    def test_rows_start_on_four_byte_boundaries(self):
        page = D.Page(D.ARTISTS)
        for i in range(5):
            page.add(D.artist_row(i + 1, "x" * i))
        self.assertTrue(all(len(r) % 4 == 0 for r in page.rows))


class Analysis(unittest.TestCase):
    def test_beats_run_to_the_end_and_follow_tempo_changes(self):
        beats = anlz.beats_from([cues.Grid(0.5, 120.0, beat=3), cues.Grid(10.5, 125.0)], 20.0)
        self.assertEqual([(b.number, b.ms) for b in beats[:3]], [(3, 500), (4, 1000), (1, 1500)])
        first_fast = next(b for b in beats if b.tempo == 12500)
        self.assertEqual((first_fast.number, first_fast.ms), (1, 10500))
        self.assertLess(beats[-1].ms, 20000)

    def test_cues(self):
        got = anlz.cues_from([cues.Mark(-0.01, "Start", 0), cues.Mark(64.0, "Loop", 1,
                                                                      cues.LOOP, 72.0),
                              cues.Mark(30.0), cues.Mark(30.0), cues.Mark(-5.0)])
        self.assertEqual([(c.hot, c.ms, c.loop_end_ms) for c in got],
                         [(0, 0, None), (1, 64000, 72000), (-1, 30000, None)])

    @unittest.skipUnless(AnlzFile, "needs pyrekordbox")
    def test_pyrekordbox_reads_it(self):
        import tempfile
        beats = anlz.beats_from([cues.Grid(0.12, 140.0)], 300.0)
        cs = anlz.cues_from([cues.Mark(0.12, "A", 0), cues.Mark(60.0),
                             cues.Mark(90.0, "L", 2, cues.LOOP, 91.714)])
        with tempfile.NamedTemporaryFile(suffix=".DAT", delete=False) as fh:
            fh.write(anlz.dat("/Contents/Ä/B/c.mp3", beats, cs))
        self.addCleanup(os.remove, fh.name)
        a = AnlzFile.parse_file(fh.name)
        self.assertEqual(a.get("path"), "/Contents/Ä/B/c.mp3")
        grid = a.get_tag("PQTZ").content.entries
        self.assertEqual(len(grid), len(beats))
        self.assertEqual((grid[3].beat, grid[3].tempo, grid[3].time), (4, 14000, 1406))
        lists = {str(t.content.cue_type): t.content.entries for t in a.getall_tags("PCOB")}
        self.assertEqual([(e.hot_cue, e.time) for e in lists["memory"]], [(0, 60000)])
        self.assertEqual([(e.hot_cue, e.time, e.loop_time) for e in lists["hotcue"]],
                         [(1, 120, 0xFFFFFFFF), (3, 90000, 91714)])


def gig_xml(path, tracks, sets):
    """A playlists file like Gig prep's: tracks [(id, file, title, artist, extra xml)]."""
    rows = []
    for tid, f, title, artist, extra in tracks:
        rows.append(f'<TRACK TrackID="{tid}" Name="{title}" Artist="{artist}" Album="Promo" '
                    f'Genre="Techno" Tonality="8A" TotalTime="30" BitRate="1411" '
                    f'SampleRate="44100" AverageBpm="128.00" '
                    f'Location="{rbxml.location(f)}">{extra}</TRACK>')
    nodes = "".join(f'<NODE Type="1" Name="{name}" KeyType="0" Entries="{len(ids)}">'
                    + "".join(f'<TRACK Key="{i}"/>' for i in ids) + "</NODE>"
                    for name, ids in sets)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'<?xml version="1.0" encoding="UTF-8"?><DJ_PLAYLISTS Version="1.0.0">'
                 f'<COLLECTION Entries="{len(tracks)}">{"".join(rows)}</COLLECTION>'
                 f'<PLAYLISTS><NODE Type="0" Name="ROOT" Count="1">'
                 f'<NODE Type="0" Name="Gig" Count="{len(sets)}">{nodes}</NODE>'
                 f'</NODE></PLAYLISTS></DJ_PLAYLISTS>')
    return path


class Stick(TempDir):
    def setUp(self):
        super().setUp()
        self.stick = self.path("GIG")
        os.makedirs(self.stick)
        music = self.path("music")
        os.makedirs(music)
        self.files = [write_wav(os.path.join(music, f"{n}.wav"), seconds=0.5)
                      for n in ("Opener", "Banger", "Closer: The End")]
        grid = '<TEMPO Inizio="0.100" Bpm="128.00" Metro="4/4" Battito="1"/>' \
               '<POSITION_MARK Name="Drop" Type="0" Start="15.100" Num="0"/>'
        self.xml = gig_xml(self.path("gig.xml"), [
            (1, self.files[0], "Opener", "Alpha", grid),
            (2, self.files[1], "Banger", "Beta", ""),
            (3, self.files[2], "Closer", "Alpha", "")],
            [("01 Warmup", [1, 2]), ("02 Peak", [3, 2])])

    def test_writes_a_library_the_stick_check_passes(self):
        res = U.write(self.stick, self.xml, log=lambda *a: None)
        self.assertEqual((res.tracks, res.playlists, len(res.copied)), (3, 2, 3))
        e = pdb.read(os.path.join(self.stick, pdb.RELATIVE))
        self.assertEqual(e.playlists, {"01 Warmup": [1, 2], "02 Peak": [3, 2]})
        for t in e.tracks:
            self.assertTrue(os.path.isfile(pdb.on_stick(self.stick, t.path)), t.path)
        self.assertEqual(e.tracks[0].path, "/Contents/Alpha/Promo/Opener.wav")
        self.assertEqual(e.tracks[2].path, "/Contents/Alpha/Promo/Closer- The End.wav")
        check, listed = V.check_export(self.stick, V.walk(self.stick), NXS2)
        self.assertEqual(check.level, V.PASS, check.summary)
        self.assertEqual(len(listed), 3)
        main = V.check_main(self.stick, NXS2, ["01 Warmup", "02 Peak"])
        self.assertEqual(main.level, V.PASS, main.summary)

    def test_every_track_gets_its_analysis_file(self):
        U.write(self.stick, self.xml, log=lambda *a: None)
        for tid in (1, 2, 3):
            p = os.path.join(self.stick, *U.anlz_path(tid).strip("/").split("/"))
            with open(p, "rb") as fh:
                self.assertEqual(fh.read(4), b"PMAI")

    def test_the_newest_players_still_need_onelibrary(self):
        U.write(self.stick, self.xml, log=lambda *a: None)
        check, _ = V.check_export(self.stick, V.walk(self.stick), P.get("cdj-3000x"))
        self.assertEqual(check.level, V.FAIL)

    def test_a_rekordbox_stick_is_never_overwritten(self):
        os.makedirs(os.path.join(self.stick, "PIONEER", "rekordbox"))
        with open(os.path.join(self.stick, pdb.RELATIVE), "wb") as fh:
            fh.write(b"rekordbox's")
        with self.assertRaises(U.StickError):
            U.write(self.stick, self.xml, log=lambda *a: None)
        with open(os.path.join(self.stick, pdb.RELATIVE), "rb") as fh:
            self.assertEqual(fh.read(), b"rekordbox's")

    def test_rekordbox_exporting_over_sorteros_library_makes_it_rekordboxs(self):
        U.write(self.stick, self.xml, log=lambda *a: None)
        with open(os.path.join(self.stick, pdb.RELATIVE), "ab") as fh:
            fh.write(b"\0" * 4096)
        self.assertIsNotNone(U.foreign(self.stick))
        with open(os.path.join(self.stick, "PIONEER", "rekordbox", "exportLibrary.db"),
                  "wb") as fh:
            fh.write(b"x")
        self.assertIn("rekordbox", U.foreign(self.stick))

    def test_a_second_run_replaces_only_its_own_files(self):
        U.write(self.stick, self.xml, log=lambda *a: None)
        mine = os.path.join(self.stick, "notes.txt")
        with open(mine, "w") as fh:
            fh.write("setlist ideas")
        gig_xml(self.xml, [(1, self.files[0], "Opener", "Alpha", "")], [("01 Short", [1])])
        res = U.write(self.stick, self.xml, log=lambda *a: None)
        self.assertEqual(res.unchanged, ["Contents/Alpha/Promo/Opener.wav"])
        self.assertEqual(sorted(res.removed), ["Contents/Alpha/Promo/Closer- The End.wav",
                                               "Contents/Beta/Promo/Banger.wav"])
        self.assertTrue(os.path.isfile(mine))
        self.assertEqual(pdb.read(os.path.join(self.stick, pdb.RELATIVE)).playlists,
                         {"01 Short": [1]})
        with open(os.path.join(self.stick, U.MARKER)) as fh:
            self.assertEqual(json.load(fh)["audio"], ["Contents/Alpha/Promo/Opener.wav"])

    def test_tracks_missing_on_this_computer_are_reported(self):
        os.remove(self.files[1])
        res = U.write(self.stick, self.xml, log=lambda *a: None)
        self.assertEqual([t for t, _ in res.missing], ["Banger"])
        e = pdb.read(os.path.join(self.stick, pdb.RELATIVE))
        self.assertEqual(e.playlists, {"01 Warmup": [1], "02 Peak": [3]})

    def test_not_enough_space(self):
        from unittest import mock
        import shutil
        with mock.patch.object(shutil, "disk_usage", return_value=shutil._ntuple_diskusage(0, 0, 10)):
            with self.assertRaises(U.StickError):
                U.write(self.stick, self.xml, log=lambda *a: None)
        self.assertFalse(os.path.exists(os.path.join(self.stick, "PIONEER")))


if __name__ == "__main__":
    unittest.main()
