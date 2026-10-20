"""Writing a rekordbox Device Library to a stick without rekordbox, read back by
independent parsers: crate-digger's for export.pdb (the one the stick check
uses), and pyrekordbox's for the analysis files when it's installed."""
import json, os, struct, unittest

from sortero.gig import anlz, cues, devicesql as D, pdb, profiles as P, usbexport as U
from sortero.gig import artwork as A, waveform as W
from sortero.gig import onelibrary as O, rbxml, verify as V
from tests.audio import write_wav
from tests.test_tagio import TempDir

try:
    from pyrekordbox.anlz import AnlzFile
except ImportError:
    AnlzFile = None
try:
    from pyrekordbox.onelibrary.database import OneLibrary as OneLibraryReader
except ImportError:
    OneLibraryReader = None

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


class Waveforms(TempDir):
    def tones(self):
        """20 s of 60 Hz, 20 s of 1 kHz, 20 s of noise above 4 kHz."""
        import shutil, subprocess
        if not shutil.which("ffmpeg"):
            self.skipTest("needs ffmpeg")
        p = self.path("bands.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=60:d=20",
                        "-f", "lavfi", "-i", "sine=f=1000:d=20", "-f", "lavfi", "-i",
                        "anoisesrc=d=20:c=white:a=0.5", "-filter_complex",
                        "[2]highpass=f=4000[n];[0][1][n]concat=n=3:v=0:a=1", "-ac", "1", p],
                       check=True)
        return p

    def test_bands_follow_the_sound(self):
        from statistics import mean
        cols = W.columns(self.tones())
        self.assertEqual(len(cols), 60 * W.PER_SECOND)
        lows, mids, highs = cols[300:2700], cols[3300:5700], cols[6300:8700]
        strongest = [max(range(3), key=lambda k: mean(c[k] for c in part))
                     for part in (lows, mids, highs)]
        self.assertEqual(strongest, [0, 1, 2])
        self.assertEqual([W._pwv5_colour(part[100]).index(7) if 7 in W._pwv5_colour(part[100])
                          else 1 for part in (lows, mids, highs)], [0, 1, 2])

    def test_rekordboxs_floors_and_ceilings(self):
        silent, loud = [(0, 0, 0, 0)] * 300, [(255, 255, 255, 255)] * 300
        self.assertEqual(set(W.pwav(silent)), {W.PWAV_FLOOR})
        self.assertEqual(set(W.pwv2(silent)), {W.PWV2_FLOOR})
        self.assertEqual(max(b & 0x1F for b in W.pwav(loud)), W.PWAV_CEILING)
        self.assertEqual(max(W.pwv2(loud)), W.PWV2_CEILING)
        self.assertEqual(max(b & 0x1F for b in W.pwv3(loud)), W.DETAIL_CEILING)
        self.assertEqual((len(W.pwav(loud)), len(W.pwv2(loud)), len(W.pwv4(loud)),
                          len(W.pwv6(loud))), (400, 100, 1200 * 6, 1200 * 3))

    def test_a_hot_cue_entry_matches_rekordboxs(self):
        # rbxport's test vector, from a rekordbox export: hot cue A at 83,032 ms
        # (the colour bytes differ: that cue had colour 46, this one the default)
        entry = anlz.pcp2(D.Cue(83032, 0)).hex()
        self.assertEqual(entry[:88], "50435032000000100000005800000001010003e800014458ffffffff"
                                     "00010000000000000000000000000000")
        self.assertEqual(entry[88:96], "2bff0017")      # A's default red

    @unittest.skipUnless(AnlzFile, "needs pyrekordbox")
    def test_the_three_files_as_rekordbox_lays_them_out(self):
        cols = [(i % 256, 100, 50, 200) for i in range(9000)]
        cs = [D.Cue(0, 0, None, "Intro"), D.Cue(15000, 3), D.Cue(30000), D.Cue(45000, 1, 48750)]
        files = anlz.files("/Contents/a.wav", [], cs, cols)
        tags = {}
        for ext, data in files.items():
            p = self.path("ANLZ0000" + ext)
            with open(p, "wb") as fh:
                fh.write(data)
            tags[ext] = AnlzFile.parse_file(p)
        self.assertEqual([t.type for t in tags[".DAT"].tags],
                         ["PPTH", "PVBR", "PQTZ", "PWAV", "PWV2", "PCOB", "PCOB"])
        self.assertEqual([t.type for t in tags[".EXT"].tags],
                         ["PPTH", "PWV3", "PCOB", "PCOB", "PCO2", "PCO2", "PWV5", "PWV4"])
        self.assertEqual([t.type for t in tags[".2EX"].tags], ["PPTH", "PWV7", "PWV6"])
        dat_hot = tags[".DAT"].getall_tags("PCOB")[0].content.entries
        self.assertEqual([e.hot_cue for e in dat_hot], [1, 2])          # A and B
        ext_hot = tags[".EXT"].getall_tags("PCOB")[0].content.entries
        self.assertEqual([e.hot_cue for e in ext_hot], [4])             # D
        named = tags[".EXT"].getall_tags("PCO2")[0].content.entries
        self.assertEqual([(e.hot_cue, e.comment) for e in named],
                         [(1, "Intro"), (2, ""), (4, "")])
        self.assertEqual(tags[".EXT"].get_tag("PWV3").content.len_entries, 9000)

    def test_without_waveforms_the_grid_and_cues_still_go(self):
        files = anlz.files("/Contents/a.mp3", [], [D.Cue(1000, 4)])
        self.assertEqual(sorted(files), [".DAT", ".EXT"])
        self.assertNotIn(b"PWAV", files[".DAT"])
        self.assertIn(b"PVBR", files[".DAT"])
        self.assertIn(b"PCO2", files[".EXT"])


class Artwork(TempDir):
    def test_where_images_go(self):
        self.assertEqual(A.path(1), "/PIONEER/Artwork/00001/a1.jpg")
        self.assertEqual(A.path(19, "b", True), "/PIONEER/Artwork/00001/b19_m.jpg")
        self.assertEqual(A.path(20), "/PIONEER/Artwork/00002/a20.jpg")

    def covered_mp3(self, name, colour):
        """An MP3 with a 500 x 500 cover of one colour."""
        import shutil, subprocess
        from mutagen.id3 import ID3, APIC
        if not shutil.which("ffmpeg"):
            self.skipTest("needs ffmpeg")
        mp3, jpg = self.path(name + ".mp3"), self.path(name + ".jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=d=1", "-c:a", "libmp3lame", mp3], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c={colour}:s=500x500", "-frames:v", "1", jpg], check=True)
        tags = ID3()
        with open(jpg, "rb") as fh:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=fh.read()))
        tags.save(mp3)
        return mp3

    def test_on_the_stick_and_in_both_libraries(self):
        from sortero.gig import tagcopy
        red1, red2 = self.covered_mp3("red1", "red"), self.covered_mp3("red2", "red")
        blue = self.covered_mp3("blue", "blue")
        bare = write_wav(self.path("bare.wav"), seconds=0.5)
        xml = gig_xml(self.path("gig.xml"), [(1, red1, "A", "X", ""), (2, red2, "B", "X", ""),
                                             (3, blue, "C", "Y", ""), (4, bare, "D", "Y", "")],
                      [("01 Set", [1, 2, 3, 4])])
        stick = self.path("GIG")
        os.makedirs(stick)
        res = U.write(stick, xml, log=lambda *a: None)
        self.assertEqual(res.artwork, 2)                # the two red covers are one image
        for image_id, px in ((1, A.SMALL), (2, A.SMALL)):
            for prefix in "ab":
                for medium, size in ((False, A.SMALL), (True, A.MEDIUM)):
                    p = os.path.join(stick, *A.path(image_id, prefix, medium).strip("/").split("/"))
                    with open(p, "rb") as fh:
                        self.assertEqual(tagcopy.image_size(fh.read())[:2], (size, size), p)
        from kaitaistruct import KaitaiStream
        from sortero.vendor.rekordbox_pdb import RekordboxPdb
        with open(os.path.join(stick, pdb.RELATIVE), "rb") as fh:
            db = RekordboxPdb(False, KaitaiStream(fh))
            art = {r.id: r.path.body.text for r in pdb._rows(db, RekordboxPdb.PageType.artwork)}
            ids = [r.artwork_id for r in pdb._rows(db, RekordboxPdb.PageType.tracks)]
        self.assertEqual(ids, [1, 1, 2, 0])
        self.assertEqual(art, {1: A.path(1), 2: A.path(2)})
        if O.available():
            con = O._open(os.path.join(stick, "PIONEER", "rekordbox", O.FILE_NAME))
            try:
                self.assertEqual(dict(con.execute("SELECT image_id, path FROM image")), art)
                self.assertEqual([r[0] for r in con.execute(
                    "SELECT image_id FROM content ORDER BY content_id")], [1, 1, 2, None])
            finally:
                con.close()


def gig_xml(path, tracks, sets):
    """A playlists file like Gig prep's: tracks [(id, file, title, artist, extra xml)],
    each optionally followed by its album (Promo otherwise)."""
    rows = []
    for tid, f, title, artist, extra, *album in tracks:
        album = album[0] if album else "Promo"
        rows.append(f'<TRACK TrackID="{tid}" Name="{title}" Artist="{artist}" Album="{album}" '
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

    def test_slashes_in_tags_and_accents_either_way_still_find_the_file(self):
        # "OFF / GRID" as an album made a folder "OFF " that FAT32 stores as "OFF", and
        # a file name with its accent decomposed (as macOS can hand it over) didn't
        # match the library's. Both left tracks "missing" from a stick just written.
        import unicodedata
        music = self.path("music")
        nfd = write_wav(os.path.join(music, unicodedata.normalize("NFD", "Holldën - Parable.wav")),
                        seconds=0.5)
        xml = gig_xml(self.path("odd.xml"), [
            (1, self.files[0], "Inner Circle", "OFF / GRID", "", "Downtown Dubs / 2 "),
            (2, nfd, "Parable", "Holldën", "", "Parable")], [("01 Odd", [1, 2])])
        U.write(self.stick, xml, log=lambda *a: None)
        paths = [t.path for t in pdb.read(os.path.join(self.stick, pdb.RELATIVE)).tracks]
        self.assertEqual(paths, ["/Contents/OFF - GRID/Downtown Dubs - 2/Opener.wav",
                                 "/Contents/Holldën/Parable/Holldën - Parable.wav"])
        self.assertEqual(paths[1], unicodedata.normalize("NFC", paths[1]))
        for p in paths:
            self.assertEqual(V.name_problems(p.lstrip("/")), [])
        check, _ = V.check_export(self.stick, V.walk(self.stick), NXS2)
        self.assertEqual(check.level, V.PASS, check.summary)

    def test_the_check_matches_accents_either_way(self):
        import unicodedata
        U.write(self.stick, self.xml, log=lambda *a: None)
        e = V.walk(self.stick)[0]
        self.assertEqual(V.name_key(unicodedata.normalize("NFD", "Ë/x")), "ë/x")
        self.assertEqual(V.name_key(e.rel), e.rel.lower())

    def test_every_track_gets_its_analysis_files(self):
        import shutil
        res = U.write(self.stick, self.xml, log=lambda *a: None)
        waves = bool(shutil.which("ffmpeg"))
        self.assertEqual(len(res.no_waveform), 0 if waves else 3)
        for tid in (1, 2, 3):
            base = os.path.join(self.stick, *U.anlz_path(tid).strip("/").split("/"))[:-4]
            for ext in (".DAT", ".EXT") + ((".2EX",) if waves else ()):
                with open(base + ext, "rb") as fh:
                    self.assertEqual(fh.read(4), b"PMAI", ext)
            with open(base + ".DAT", "rb") as fh:
                self.assertEqual(b"PWAV" in fh.read(), waves)

    def test_writing_again_reuses_waveforms_made_from_this_computers_copy(self):
        import shutil
        from unittest import mock
        if not shutil.which("ffmpeg"):
            self.skipTest("needs ffmpeg")
        cache = W.Cache(self.path("waves"))
        decoded = []
        real = W.ffmpeg.run

        def run(args, *a, **k):
            decoded.append(args[args.index("-i") + 1])
            return real(args, *a, **k)
        with mock.patch.object(W.ffmpeg, "run", run):
            U.write(self.stick, self.xml, log=lambda *a: None, cache=cache)
            self.assertEqual(sorted(decoded), sorted(self.files))   # never the stick's
            decoded.clear()
            res = U.write(self.stick, self.xml, log=lambda *a: None, cache=cache)
            self.assertEqual((decoded, res.no_waveform), ([], []))
            write_wav(self.files[1], seconds=0.7)                   # a changed file
            U.write(self.stick, self.xml, log=lambda *a: None, cache=cache)
            self.assertEqual(decoded, [self.files[1]])

    def test_a_remembered_waveform_reads_back_as_it_was_made(self):
        cache = W.Cache(self.path("waves"))
        waves = W.encode([(1, 2, 3, 4), (255, 0, 128, 7)])
        cache.put(self.files[0], waves)
        self.assertEqual(cache.get(self.files[0]), waves)
        self.assertIsNone(cache.get(self.files[1]))

    @unittest.skipUnless(O.available(), "needs sqlcipher3")
    def test_onelibrary_for_the_newest_players(self):
        res = U.write(self.stick, self.xml, log=lambda *a: None)
        self.assertTrue(res.onelibrary)
        ol = os.path.join(self.stick, "PIONEER", "rekordbox", O.FILE_NAME)
        tracks, lists = O.read(ol)
        e = pdb.read(os.path.join(self.stick, pdb.RELATIVE))
        self.assertEqual([(i, p, t) for i, p, t, _ in tracks],
                         [(t.id, t.path, t.title) for t in e.tracks])
        self.assertEqual(lists, e.playlists)
        check, _ = V.check_export(self.stick, V.walk(self.stick), P.get("cdj-3000x"))
        self.assertEqual(check.level, V.PASS, check.summary)
        check, _ = V.check_export(self.stick, V.walk(self.stick), P.EVERY)
        self.assertEqual(check.level, V.PASS, check.summary)
        self.assertIsNone(U.foreign(self.stick))    # still Sortero's to replace

    @unittest.skipUnless(O.available() and OneLibraryReader, "needs sqlcipher3 and pyrekordbox")
    def test_pyrekordbox_reads_the_onelibrary(self):
        U.write(self.stick, self.xml, log=lambda *a: None)
        db = OneLibraryReader(os.path.join(self.stick, "PIONEER", "rekordbox", O.FILE_NAME))
        titles = sorted(c.title for c in db.get_content().all())
        self.assertEqual(titles, ["Banger", "Closer", "Opener"])
        cue = db.get_cue().one()
        self.assertEqual((cue.kind, cue.inUsec), (1, 15_100_000))      # hot cue A
        self.assertEqual(sorted(p.name for p in db.get_playlist().all()),
                         ["01 Warmup", "02 Peak", "Gig"])

    def test_without_sqlcipher_the_newest_players_are_told_its_missing(self):
        from unittest import mock
        with mock.patch.object(O, "available", return_value=False):
            res = U.write(self.stick, self.xml, log=lambda *a: None)
        self.assertFalse(res.onelibrary)
        self.assertFalse(os.path.exists(os.path.join(self.stick, "PIONEER", "rekordbox",
                                                     O.FILE_NAME)))
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
