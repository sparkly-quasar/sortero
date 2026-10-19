"""Cue points from rekordbox, Mixxx and Mixed In Key, and the rekordbox XML of a gig."""
import base64, json, os, sqlite3, unittest
import xml.etree.ElementTree as ET

from mutagen.id3 import GEOB, ID3, TBPM, TIT2, TKEY, TPE1

from sortero import mixxx
from sortero.gig import convert as C, cues, rbxml
from sortero.gig.profiles import AudioInfo
from tests.test_tagio import TempDir


def mik(obj):
    b64 = base64.b64encode(json.dumps({**obj, "source": "mixedinkey"}).encode()).decode()
    return ("".join(b64[i:i + 72] + "\n" for i in range(0, len(b64), 72))).encode()


def tagged_mp3(path, title="Quasar", artist="Daryn", key="Cm", bpm="140", mik_cues=None,
               grid=None):
    """A file with an ID3 tag only: enough for tags and cues, no audio needed."""
    with open(path, "wb") as fh:
        fh.write(b"\0" * 512)
    t = ID3()
    for fr in (TIT2(encoding=3, text=[title]), TPE1(encoding=3, text=[artist]),
               TKEY(encoding=3, text=[key]), TBPM(encoding=3, text=[bpm])):
        t.add(fr)
    if mik_cues is not None:
        t.add(GEOB(encoding=3, mime="application/json", desc="CuePoints", filename="",
                   data=mik({"algorithm": 14, "cues": mik_cues})))
    if grid is not None:
        t.add(GEOB(encoding=3, mime="application/json", desc="BeatGrid", filename="",
                   data=mik({"algorithm": 12, "tempo": grid[1], "beats": [grid[0]]})))
    t.save(path, v2_version=3)
    return path


MIK_CUES = [{"name": f"Energy {i}", "time": 1000.0 * i} for i in range(1, 11)]


class MixedInKey(TempDir):
    def test_cues_in_milliseconds_become_memory_and_hot_cues(self):
        p = tagged_mp3(self.path("a.mp3"), mik_cues=MIK_CUES, grid=(0.0089, 140.0))
        m = cues.from_mik(p)
        memory = [x for x in m.marks if x.num == cues.MEMORY]
        hot = [x for x in m.marks if x.num != cues.MEMORY]
        self.assertEqual([x.start for x in memory], [float(i) for i in range(1, 11)])
        self.assertEqual([x.num for x in hot], list(range(8)))     # A-H, first eight
        self.assertEqual(hot[0].name, "Energy 1")
        self.assertEqual((m.grids[0].start, m.grids[0].bpm), (0.0089, 140.0))

    def test_no_analysis_is_none(self):
        self.assertIsNone(cues.from_mik(tagged_mp3(self.path("b.mp3"))))


class Rekordbox(TempDir):
    def test_reads_cues_and_grids_by_location(self):
        target = self.path("My Music/Track & Co é.mp3")
        xml = self.path("collection.xml")
        with open(xml, "w", encoding="utf-8") as fh:
            fh.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0"><COLLECTION Entries="1">
<TRACK TrackID="7" Name="T" Location="{rbxml.location(target)}">
  <TEMPO Inizio="0.025" Bpm="128.00" Metro="4/4" Battito="1"/>
  <POSITION_MARK Name="Drop" Type="0" Start="60.123" Num="2"/>
  <POSITION_MARK Name="" Type="4" Start="30.000" End="37.500" Num="-1"/>
</TRACK></COLLECTION></DJ_PLAYLISTS>""")
        found = cues.from_rekordbox_xml(xml)
        m = found[cues.key(target)]
        self.assertEqual(m.source, "rekordbox")
        self.assertEqual([(x.start, x.num, x.type, x.end) for x in m.marks],
                         [(60.123, 2, cues.CUE, None), (30.0, -1, cues.LOOP, 37.5)])
        self.assertEqual((m.grids[0].start, m.grids[0].bpm), (0.025, 128.0))

    def test_location_round_trip(self):
        p = "/Users/dj/My Music/Track & Co é.mp3"
        loc = rbxml.location(p)
        self.assertEqual(loc, "file://localhost/Users/dj/My%20Music/Track%20%26%20Co%20%C3%A9.mp3")
        self.assertEqual(cues.location_path(loc), p)
        self.assertEqual(cues.location_path("file://localhost/C:/Music/Demo%20Track.mp3"),
                         os.path.normpath("C:/Music/Demo Track.mp3"))


class Mixxx(TempDir):
    def db(self, track_path, rate=44100):
        p = self.path("mixxxdb.sqlite")
        con = sqlite3.connect(p)
        con.executescript("""
            CREATE TABLE track_locations (id INTEGER PRIMARY KEY, location TEXT);
            CREATE TABLE library (id INTEGER PRIMARY KEY, location INTEGER, samplerate INTEGER,
                                  bpm REAL, beats BLOB, beats_version TEXT);
            CREATE TABLE cues (id INTEGER PRIMARY KEY, track_id INTEGER, type INTEGER,
                               position REAL, length REAL, hotcue INTEGER, label TEXT,
                               color INTEGER);""")
        con.execute("INSERT INTO track_locations VALUES (1, ?)", (track_path,))
        con.execute("INSERT INTO library VALUES (1, 1, ?, 126.0, ?, ?)",
                    (rate, mixxx.grid_blob(b"", 126.0, rate // 2), mixxx.GRID))
        # positions are frames x 2 ("engine samples")
        con.executemany("INSERT INTO cues (track_id, type, position, length, hotcue, label) "
                        "VALUES (1, ?, ?, ?, ?, ?)",
                        [(1, 2 * rate * 10, 0, 0, "Intro"),     # hot cue A at 10 s
                         (2, 2 * rate * 1, 0, -1, ""),          # main cue at 1 s
                         (4, 2 * rate * 20, 2 * rate * 4, 1, "Loop"),   # loop 20-24 s
                         (6, 2 * rate * 5, 0, -1, "")])         # intro: not carried
        con.commit()
        con.close()
        return p

    def test_reads_cues_loops_and_grid(self):
        track = self.path("t.flac")
        m = cues.from_mixxx(self.db(track))[cues.key(track)]
        self.assertEqual(sorted((x.start, x.num, x.type, x.end) for x in m.marks),
                         [(1.0, -1, cues.CUE, None), (10.0, 0, cues.CUE, None),
                          (20.0, 1, cues.LOOP, 24.0)])
        self.assertEqual((m.grids[0].start, m.grids[0].bpm), (0.5, 126.0))


class Priority(TempDir):
    def test_rekordbox_then_mik_then_mixxx(self):
        bare = tagged_mp3(self.path("bare.mp3"))
        self.assertEqual(cues.Finder(mixxx_db=Mixxx.db(self, bare)).find(bare).source,
                         "Mixxx")
        os.remove(self.path("mixxxdb.sqlite"))
        track = tagged_mp3(self.path("t.mp3"), mik_cues=MIK_CUES)
        self.assertEqual(cues.Finder().find(track).source, "Mixed In Key")
        f = cues.Finder(mixxx_db=Mixxx.db(self, track))
        self.assertEqual(f.find(track).source, "Mixed In Key")
        xml = self.path("c.xml")
        with open(xml, "w") as fh:
            fh.write(f'<DJ_PLAYLISTS><COLLECTION><TRACK Location="{rbxml.location(track)}">'
                     '<POSITION_MARK Type="0" Start="1.0" Num="-1"/></TRACK></COLLECTION>'
                     '</DJ_PLAYLISTS>')
        f = cues.Finder(rekordbox_xml=xml, mixxx_db=self.path("mixxxdb.sqlite"))
        self.assertEqual(f.find(track).source, "rekordbox")

    def test_cues_and_grid_each_from_the_first_source_that_has_them(self):
        # Mixed In Key has cues but no grid for this track; Mixxx has both.
        track = tagged_mp3(self.path("t.mp3"), mik_cues=MIK_CUES)
        m = cues.Finder(mixxx_db=Mixxx.db(self, track)).find(track)
        self.assertEqual((m.source, m.grid_from), ("Mixed In Key", "Mixxx"))
        self.assertEqual(m.marks[0].name, "Energy 1")
        self.assertEqual(m.grids[0].bpm, 126.0)
        counts = cues.survey([track, track], cues.Finder(mixxx_db=self.path("mixxxdb.sqlite")))
        self.assertEqual(counts, {"cues": {"Mixed In Key": 1}, "grids": {"Mixxx": 1}})

    def test_unreadable_sources_are_logged_not_fatal(self):
        bad = self.path("bad.xml")
        with open(bad, "w") as fh:
            fh.write("<DJ_PLAYLISTS><oops")
        logged = []
        f = cues.Finder(rekordbox_xml=bad, log=logged.append)
        self.assertTrue(logged)
        self.assertIsNone(f.find(tagged_mp3(self.path("x.mp3"))))


class Build(TempDir):
    def setUp(self):
        super().setUp()
        os.makedirs(self.path("Collection"))
        os.makedirs(self.path("Library"))
        self.original = tagged_mp3(self.path("Collection/Quasar.mp3"), mik_cues=MIK_CUES,
                                   grid=(0.0089, 140.0))
        # A FLAC original (a stand-in carrying the same tags) and its MP3 copy.
        # Cues are looked up on the original, as Gig prep does.
        self.flac = tagged_mp3(self.path("Collection/Ninth Hour.flac"),
                               mik_cues=[{"name": "Energy 6", "time": 2000.0}])
        self.copy = tagged_mp3(self.path("Library/Ninth Hour.mp3"), title="Ninth Hour",
                               artist="Kepler Vale", key="8A", bpm="128")
        info = AudioInfo("mp3", ".mp3", 44100, kbps=320, extras={"duration": 346.6})
        self.items = [C.Item(self.original, "Quasar.mp3", C.READY, info),
                      C.Item(self.flac, "Ninth Hour.flac", C.CONVERTED, info, C.MP3,
                             self.copy)]
        self.sets = [("Warmup", [self.flac]), ("Peak", [self.original, self.flac])]

    def build(self, shift=False):
        tree, counts = rbxml.build(self.items, self.sets, "Sortero gig 2026-10-14",
                                   cues.Finder(), shift)
        path = rbxml.write(self.path("gig.xml"), tree)
        return ET.parse(path).getroot(), counts

    def test_structure_follows_the_rekordbox_format(self):
        root, counts = self.build()
        self.assertEqual(root.tag, "DJ_PLAYLISTS")
        self.assertEqual(root.find("PRODUCT").get("Name"), "Sortero")
        col = root.find("COLLECTION")
        tracks = col.findall("TRACK")
        self.assertEqual(col.get("Entries"), "2")           # the shared track once
        self.assertEqual([t.get("Location") for t in tracks],
                         [rbxml.location(self.copy), rbxml.location(self.original)])
        q = tracks[1]
        self.assertEqual((q.get("Name"), q.get("Artist"), q.get("Tonality"),
                          q.get("AverageBpm"), q.get("TotalTime"), q.get("BitRate")),
                         ("Quasar", "Daryn", "Cm", "140.00", "346", "320"))
        self.assertEqual(q.find("TEMPO").attrib, {"Inizio": "0.009", "Bpm": "140.00",
                                                  "Metro": "4/4", "Battito": "1"})
        marks = q.findall("POSITION_MARK")
        self.assertEqual(len(marks), 18)                    # 10 memory + 8 hot
        self.assertEqual(marks[0].attrib, {"Name": "Energy 1", "Type": "0",
                                           "Start": "1.000", "Num": "-1"})
        self.assertEqual(counts["cues"], {"Mixed In Key": 2})

        folder = root.find("PLAYLISTS/NODE")
        self.assertEqual((folder.get("Name"), folder.get("Type")), ("ROOT", "0"))
        gig = folder.find("NODE")
        self.assertEqual(gig.get("Name"), "Sortero gig 2026-10-14")
        lists = gig.findall("NODE")
        self.assertEqual([(n.get("Name"), n.get("Type"), n.get("KeyType"), n.get("Entries"))
                          for n in lists],
                         [("01 Warmup", "1", "0", "1"), ("02 Peak", "1", "0", "2")])
        self.assertEqual([t.get("Key") for t in lists[1].findall("TRACK")], ["2", "1"])

    def test_cues_on_mp3_copies_move_only_when_asked(self):
        root, _ = self.build(shift=False)
        copy = root.find("COLLECTION/TRACK")
        self.assertEqual(copy.find("POSITION_MARK").get("Start"), "2.000")
        root, _ = self.build(shift=True)
        copy, original = root.findall("COLLECTION/TRACK")
        self.assertEqual(copy.find("POSITION_MARK").get("Start"), "2.025")
        self.assertEqual(original.find("POSITION_MARK").get("Start"), "1.000")

    def test_how_to_names_the_file(self):
        steps = "\n".join(rbxml.how_to("/Gig Library/Sortero gig.xml"))
        self.assertIn("/Gig Library/Sortero gig.xml", steps)
        self.assertIn("7.2.11", steps)


if __name__ == "__main__":
    unittest.main()
