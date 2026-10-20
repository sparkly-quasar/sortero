"""Where Gig prep's playlists come from: Sortero's own, playlist files, folders,
rekordbox XML, Mixxx and iTunes."""
import os, plistlib, sqlite3, unittest

from sortero import playlists, settings
from sortero.gig import backup, convert, sources as S
from tests.test_tagio import TempDir


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()
    return path


class M3U(TempDir):
    def write(self, name, data):
        p = self.path(name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def test_relative_paths_are_from_the_playlist_folder(self):
        p = self.write("set.m3u8", b"#EXTM3U\n#EXTINF:300,A - One\nmusic/a.mp3\n\n"
                                   b"../elsewhere/b.mp3\n")
        self.assertEqual(S.read_m3u(p), [self.path("music/a.mp3"),
                                         os.path.normpath(self.path("../elsewhere/b.mp3"))])

    def test_absolute_paths_and_file_urls(self):
        p = self.write("set.m3u8", "/music/One.mp3\nfile:///music/Two%20Three.mp3\n"
                                   "file://localhost/music/Four.mp3\n".encode())
        self.assertEqual(S.read_m3u(p), [os.path.normpath(x) for x in
                                         ("/music/One.mp3", "/music/Two Three.mp3",
                                          "/music/Four.mp3")])

    def test_utf8_with_a_bom(self):
        p = self.write("set.m3u8", "\ufeffBjörk - Jóga.mp3\n".encode("utf-8"))
        self.assertEqual(S.read_m3u(p), [self.path("Björk - Jóga.mp3")])

    def test_old_m3u_in_windows_1252(self):
        p = self.write("set.m3u", "Café del Mar – Ibiza.mp3\n".encode("cp1252"))
        self.assertEqual(S.read_m3u(p), [self.path("Café del Mar – Ibiza.mp3")])

    def test_apple_music_bare_carriage_returns(self):
        p = self.write("set.m3u", b"#EXTM3U\r#EXTINF:1,x\r/m/a.mp3\r/m/b.mp3\r")
        self.assertEqual(S.read_m3u(p), [os.path.normpath("/m/a.mp3"),
                                         os.path.normpath("/m/b.mp3")])

    def test_stream_urls_are_skipped(self):
        p = self.write("set.m3u8", b"https://radio.example/stream\na.mp3\n")
        self.assertEqual(S.read_m3u(p), [self.path("a.mp3")])

    @unittest.skipIf(os.sep != "/", "backslashes are already separators on Windows")
    def test_windows_paths_on_a_mac(self):
        p = self.write("set.m3u", b"Sets\\Peak\\a.mp3\nC:\\Music\\b.mp3\n")
        got = S.read_m3u(p)
        self.assertEqual(got[0], self.path("Sets/Peak/a.mp3"))
        self.assertEqual(got[1], "C:\\Music\\b.mp3")      # kept, so it shows as missing

    def test_find_every_playlist_in_a_folder(self):
        for rel in ("10 Closing.m3u8", "2 Peak.M3U", "sub/1 Warmup.m3u", "notes.txt",
                    "._2 Peak.m3u", ".hidden/x.m3u8"):
            touch(self.path("pl/" + rel))
        found = [os.path.relpath(p, self.path("pl")) for p in S.find_m3u(self.path("pl"))]
        self.assertEqual(found, ["2 Peak.M3U", "10 Closing.m3u8",
                                 os.path.join("sub", "1 Warmup.m3u")])
        self.assertEqual([s.name for s in S.m3u_sources(S.find_m3u(self.path("pl")))],
                         ["2 Peak", "10 Closing", "1 Warmup"])


class Folders(TempDir):
    def test_tracks_in_filename_order(self):
        for rel in ("10 Outro.mp3", "2 Intro.flac", "1 Open.aiff", "cover.jpg",
                    "._1 Open.aiff", "b/1 Deeper.wav"):
            touch(self.path("Gig/" + rel))
        got = [os.path.relpath(p, self.path("Gig")) for p in
               S.folder_tracks(self.path("Gig"))]
        self.assertEqual(got, ["1 Open.aiff", "2 Intro.flac", "10 Outro.mp3",
                               os.path.join("b", "1 Deeper.wav")])

    def test_named_after_the_folder(self):
        self.assertEqual(S.folder_source(self.path("Gig 18 Oct") + os.sep).name,
                         "Gig 18 Oct")

    def test_a_folder_that_has_gone(self):
        src = S.folder_source(self.path("gone"))
        with self.assertRaises(S.SourceError):
            S.tracks(src)


REKORDBOX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <COLLECTION Entries="3">
    <TRACK TrackID="1" Name="One" Location="file://localhost/music/One.mp3">
      <POSITION_MARK Name="" Type="0" Start="1.0" Num="0"/>
    </TRACK>
    <TRACK TrackID="2" Name="Two" Location="file://localhost/music/Two%20B.flac"/>
    <TRACK TrackID="3" Name="Three" Location="file://localhost/music/Three.wav"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Warmup" Type="1" KeyType="0" Entries="2">
        <TRACK Key="3"/><TRACK Key="1"/>
      </NODE>
      <NODE Type="0" Name="Gigs" Count="1">
        <NODE Type="0" Name="October" Count="1">
          <NODE Name="Peak" Type="1" KeyType="0" Entries="3">
            <TRACK Key="2"/><TRACK Key="1"/><TRACK Key="99"/>
          </NODE>
        </NODE>
        <NODE Name="By location" Type="1" KeyType="1" Entries="1">
          <TRACK Key="file://localhost/music/Three.wav"/>
        </NODE>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""


class Rekordbox(TempDir):
    def setUp(self):
        super().setUp()
        self.xml = self.path("rekordbox.xml")
        with open(self.xml, "w", encoding="utf-8") as fh:
            fh.write(REKORDBOX_XML)

    def test_every_playlist_with_its_folders(self):
        found = S.rekordbox_sources(self.xml)
        self.assertEqual([(s.name, s.ref) for s in found],
                         [("Warmup", ("Warmup",)), ("Peak", ("Gigs", "October", "Peak")),
                          ("By location", ("Gigs", "By location"))])
        self.assertEqual(found[1].where, "rekordbox: Gigs › October")

    def test_tracks_in_playlist_order(self):
        found = {s.name: s for s in S.rekordbox_sources(self.xml)}
        n = os.path.normpath
        self.assertEqual(S.tracks(found["Warmup"]), [n("/music/Three.wav"), n("/music/One.mp3")])
        # a key with no track in the collection is left out
        self.assertEqual(S.tracks(found["Peak"]), [n("/music/Two B.flac"), n("/music/One.mp3")])
        self.assertEqual(S.tracks(found["By location"]), [n("/music/Three.wav")])

    def test_rereads_a_fresh_export(self):
        peak = S.rekordbox_sources(self.xml)[1]
        with open(self.xml, "w", encoding="utf-8") as fh:
            fh.write(REKORDBOX_XML.replace('<TRACK Key="2"/><TRACK Key="1"/>',
                                           '<TRACK Key="1"/>'))
        os.utime(self.xml, ns=(1, 10 ** 18))      # a different mtime, however fast
        self.assertEqual(S.tracks(peak), [os.path.normpath("/music/One.mp3")])

    def test_a_playlist_deleted_since(self):
        gone = S.Source(S.REKORDBOX, self.xml, "Old", ("Old",))
        with self.assertRaises(S.SourceError):
            S.tracks(gone)

    def test_not_an_xml(self):
        bad = self.path("bad.xml")
        with open(bad, "w") as fh:
            fh.write("<DJ_PLAYLISTS><oops")
        with self.assertRaises(S.SourceError):
            S.rekordbox_sources(bad)


class Mixxx(TempDir):
    def setUp(self):
        super().setUp()
        self.db = self.path("mixxxdb.sqlite")
        con = sqlite3.connect(self.db)
        con.executescript("""
            CREATE TABLE track_locations (id INTEGER PRIMARY KEY, location TEXT);
            CREATE TABLE library (id INTEGER PRIMARY KEY, location INTEGER);
            CREATE TABLE Playlists (id INTEGER PRIMARY KEY, name TEXT, position INTEGER,
                                    hidden INTEGER DEFAULT 0 NOT NULL);
            CREATE TABLE PlaylistTracks (id INTEGER PRIMARY KEY, playlist_id INTEGER,
                                         track_id INTEGER, position INTEGER);
            CREATE TABLE crates (id INTEGER PRIMARY KEY, name TEXT, show INTEGER);
            CREATE TABLE crate_tracks (crate_id INTEGER, track_id INTEGER);
            INSERT INTO track_locations VALUES (1, '/m/b 10.mp3'), (2, '/m/a.mp3'),
                                               (3, '/m/b 9.mp3');
            INSERT INTO library VALUES (11, 1), (12, 2), (13, 3);
            INSERT INTO Playlists VALUES (1, 'Peak', 2, 0), (2, 'Auto DJ', 0, 1),
                                         (3, 'Warmup', 1, 0), (4, '2026-10-14', 3, 2);
            INSERT INTO PlaylistTracks (playlist_id, track_id, position)
                VALUES (1, 13, 2), (1, 11, 1), (3, 12, 1);
            INSERT INTO crates VALUES (1, 'techno', 1), (2, 'Deep', 1);
            INSERT INTO crate_tracks VALUES (1, 11), (1, 13), (1, 12);""")
        con.commit()
        con.close()

    def test_playlists_then_crates_without_auto_dj_or_history(self):
        found = S.mixxx_sources(self.db)
        self.assertEqual([(s.name, s.ref[0]) for s in found],
                         [("Warmup", "playlist"), ("Peak", "playlist"),
                          ("Deep", "crate"), ("techno", "crate")])

    def test_playlist_order_and_crates_by_filename(self):
        found = {s.name: s for s in S.mixxx_sources(self.db)}
        n = os.path.normpath
        self.assertEqual(S.tracks(found["Peak"]), [n("/m/b 10.mp3"), n("/m/b 9.mp3")])
        self.assertEqual(S.tracks(found["techno"]),
                         [n("/m/a.mp3"), n("/m/b 9.mp3"), n("/m/b 10.mp3")])

    def test_a_deleted_playlist(self):
        with self.assertRaises(S.SourceError):
            S.tracks(S.Source(S.MIXXX, self.db, "Gone", ("playlist", 99)))

    def test_no_library(self):
        with self.assertRaises(S.SourceError):
            S.mixxx_sources(self.path("nope.sqlite"))


def itunes_library(**changes):
    """A library XML as Music writes it: built-in playlists, a folder, a smart
    playlist, and a track that's only in the cloud."""
    loc = "file://localhost/music/"
    lib = {
        "Major Version": 1, "Minor Version": 1, "Application Version": "1.4",
        "Tracks": {
            "101": {"Track ID": 101, "Name": "One", "Location": loc + "One.mp3"},
            "102": {"Track ID": 102, "Name": "Two", "Location": loc + "Two%20B.m4a"},
            "103": {"Track ID": 103, "Name": "Streamed"},
        },
        "Playlists": [
            {"Name": "Library", "Master": True, "Visible": False,
             "Playlist Persistent ID": "AAAA0000AAAA0000",
             "Playlist Items": [{"Track ID": 101}, {"Track ID": 102}]},
            {"Name": "Music", "Distinguished Kind": 4,
             "Playlist Persistent ID": "BBBB0000BBBB0000",
             "Playlist Items": [{"Track ID": 101}]},
            {"Name": "Gigs", "Folder": True, "Playlist Persistent ID": "F1F1F1F1F1F1F1F1",
             "Playlist Items": [{"Track ID": 102}]},
            {"Name": "October", "Folder": True, "Playlist Persistent ID": "F2F2F2F2F2F2F2F2",
             "Parent Persistent ID": "F1F1F1F1F1F1F1F1"},
            {"Name": "Peak", "Playlist Persistent ID": "C0C0C0C0C0C0C0C0",
             "Parent Persistent ID": "F2F2F2F2F2F2F2F2",
             "Playlist Items": [{"Track ID": 102}, {"Track ID": 103}, {"Track ID": 101}]},
            {"Name": "Fresh techno", "Playlist Persistent ID": "5E5E5E5E5E5E5E5E",
             "Smart Info": b"\x01", "Smart Criteria": b"\x02",
             "Playlist Items": [{"Track ID": 101}]},
            {"Name": "Empty", "Playlist Persistent ID": "E0E0E0E0E0E0E0E0"},
        ],
    }
    lib.update(changes)
    return lib


class ITunes(TempDir):
    def setUp(self):
        super().setUp()
        self.xml = self.write(itunes_library())

    def write(self, lib, name="Library.xml"):
        p = self.path(name)
        with open(p, "wb") as fh:
            plistlib.dump(lib, fh)
        os.utime(p, ns=(1, os.stat(p).st_mtime_ns + 10 ** 9))   # a fresh mtime
        return p

    def test_playlists_without_the_built_in_ones_or_folders(self):
        found = S.itunes_sources(self.xml)
        self.assertEqual([(s.name, s.ref) for s in found],
                         [("Peak", ("C0C0C0C0C0C0C0C0", "Gigs", "October")),
                          ("Fresh techno", ("5E5E5E5E5E5E5E5E",)),
                          ("Empty", ("E0E0E0E0E0E0E0E0",))])
        self.assertEqual(found[0].where, "iTunes/Music: Gigs › October")
        self.assertEqual(found[1].where, "iTunes/Music")

    def test_tracks_in_playlist_order_leaving_out_cloud_only_ones(self):
        found = {s.name: s for s in S.itunes_sources(self.xml)}
        n = os.path.normpath
        self.assertEqual(S.tracks(found["Peak"]), [n("/music/Two B.m4a"), n("/music/One.mp3")])
        self.assertEqual(S.tracks(found["Fresh techno"]), [n("/music/One.mp3")])
        self.assertEqual(S.tracks(found["Empty"]), [])

    def test_windows_locations(self):
        lib = itunes_library()
        lib["Tracks"]["101"]["Location"] = "file://localhost/C:/Users/dj/Music/One%20(Edit).mp3"
        found = {s.name: s for s in S.itunes_sources(self.write(lib))}
        self.assertEqual(S.tracks(found["Fresh techno"]),
                         [os.path.normpath("C:/Users/dj/Music/One (Edit).mp3")])

    def test_a_smart_playlist_that_matches_more_later(self):
        smart = {s.name: s for s in S.itunes_sources(self.xml)}["Fresh techno"]
        lib = itunes_library()
        lib["Playlists"][5]["Playlist Items"] = [{"Track ID": 102}, {"Track ID": 101}]
        self.write(lib)
        self.assertEqual(S.tracks(smart), [os.path.normpath("/music/Two B.m4a"),
                                           os.path.normpath("/music/One.mp3")])

    def test_renamed_or_moved_still_found_by_its_id(self):
        peak = S.itunes_sources(self.xml)[0]
        lib = itunes_library()
        lib["Playlists"][4]["Name"] = "Peak time"
        del lib["Playlists"][4]["Parent Persistent ID"]     # out of its folder
        self.write(lib)
        self.assertEqual(len(S.tracks(peak)), 2)

    def test_a_playlist_deleted_since(self):
        gone = S.Source(S.ITUNES, self.xml, "Old", ("0123456789ABCDEF",))
        with self.assertRaises(S.SourceError):
            S.tracks(gone)

    def test_not_a_library(self):
        bad = self.path("bad.xml")
        with open(bad, "w") as fh:
            fh.write("<plist><dict><key>Tracks")
        with self.assertRaises(S.SourceError):
            S.itunes_sources(bad)
        with self.assertRaises(S.SourceError):
            S.itunes_sources(self.path("nope.xml"))

    def test_ids_survive_the_round_trip(self):
        src = S.itunes_sources(self.xml)[0]
        self.assertEqual(S.Source.from_dict(src.to_dict()).key, src.key)


class Remembering(TempDir):
    def setUp(self):
        super().setUp()
        settings.set("gig_sources", [])
        self.addCleanup(settings.set, "gig_sources", [])

    def test_added_once_and_forgotten(self):
        a, b = S.m3u_sources([self.path("a.m3u8"), self.path("b.m3u")])
        self.assertEqual(S.remember([a]), [a])
        self.assertEqual(S.remember([a, b, b]), [b])
        self.assertEqual(S.added(), [a, b])
        S.forget(a.key)
        self.assertEqual(S.added(), [b])

    def test_mixxx_ids_survive_the_round_trip(self):
        src = S.Source(S.MIXXX, "/db", "Peak", ("playlist", 3))
        S.remember([src])
        self.assertEqual(S.added()[0].key, src.key)

    def test_sortero_playlists_first_keyed_by_name(self):
        root = self.path("Music")
        a = touch(os.path.join(root, "Techno", "a.mp3"))
        playlists.write(root, "Warmup", [a])
        extra = S.folder_source(os.path.join(root, "Techno"))
        S.remember([extra])
        got = S.listed(root)
        self.assertEqual([(s.kind, s.key == s.name) for s in got],
                         [(S.SORTERO, True), (S.FOLDER, False)])
        self.assertEqual(S.tracks(got[0], root), [a])

    def test_one_unreadable_playlist_doesnt_stop_the_rest(self):
        root = self.path("Music")
        a = touch(os.path.join(root, "a.mp3"))
        playlists.write(root, "Warmup", [a])
        said = []
        sets = S.resolve([S.Source(S.SORTERO, "", "Warmup"),
                          S.folder_source(self.path("gone"))], root, log=said.append)
        self.assertEqual(sets, [("Warmup", [a])])
        self.assertIn("gone", said[0])


class Watching(TempDir):
    def setUp(self):
        super().setUp()
        for key in ("gig_sources", "gig_watch_folders"):
            settings.set(key, [])
            self.addCleanup(settings.set, key, [])
        self.exports = self.path("Exports")
        touch(os.path.join(self.exports, "Warmup.m3u8"))

    def test_a_playlist_saved_later_turns_up(self):
        self.assertTrue(S.watch(self.exports))
        self.assertEqual([s.name for s in S.listed(None)], ["Warmup"])
        touch(os.path.join(self.exports, "Peak", "Peak time.m3u"))
        got = S.listed(None)
        self.assertEqual([(s.kind, s.name) for s in got],
                         [(S.WATCHED, "Peak time"), (S.WATCHED, "Warmup")])
        self.assertEqual({s.folder for s in got}, {os.path.abspath(self.exports)})

    def test_watched_once_and_unwatched(self):
        self.assertTrue(S.watch(self.exports))
        self.assertFalse(S.watch(self.exports + os.sep))
        S.unwatch(os.path.abspath(self.exports))
        self.assertEqual(S.listed(None), [])

    def test_keys_stay_the_same_between_looks(self):
        S.watch(self.exports)
        self.assertEqual([s.key for s in S.listed(None)], [s.key for s in S.listed(None)])

    def test_tracks_read_like_any_playlist_file(self):
        a = touch(os.path.join(self.exports, "music", "a.mp3"))
        with open(os.path.join(self.exports, "Warmup.m3u8"), "w") as fh:
            fh.write("#EXTM3U\nmusic/a.mp3\n")
        S.watch(self.exports)
        self.assertEqual(S.tracks(S.listed(None)[0]), [a])

    def test_a_missing_folder_lists_nothing_and_is_reported(self):
        gone = self.path("Unplugged")
        S.watch(gone)
        self.assertEqual(S.listed(None), [])
        self.assertEqual(S.missing_watched(), [gone])

    def test_not_listed_twice(self):
        root = self.path("Music")
        a = touch(os.path.join(root, "Techno", "a.mp3"))
        playlists.write(root, "Warmup", [a])
        S.watch(playlists.playlist_dir(root))           # Sortero's own folder
        S.watch(self.exports)
        S.remember(S.m3u_sources([os.path.join(self.exports, "Warmup.m3u8")]))
        self.assertEqual([s.kind for s in S.listed(root)], [S.SORTERO, S.M3U])


class InTheCollection(TempDir):
    def setUp(self):
        super().setUp()
        for key, empty in (("gig_sources", []), ("gig_watch_folders", []),
                           ("gig_search_collection", None)):
            settings.set(key, empty)
            self.addCleanup(settings.set, key, empty)
        self.root = self.path("Music")
        a = touch(os.path.join(self.root, "Techno", "a.mp3"))
        playlists.write(self.root, "Warmup", [a])                  # Sortero's own
        touch(os.path.join(self.root, "_Quarantine", "old.m3u8"))  # set aside
        self.navi = touch(os.path.join(self.root, "_Inbox", "navidrome", "Peak.m3u8"))

    def test_found_anywhere_but_playlists_and_quarantine(self):
        got = S.listed(self.root)
        self.assertEqual([(s.kind, s.name) for s in got],
                         [(S.SORTERO, "Warmup"), (S.COLLECTION, "Peak")])
        self.assertEqual(got[1].path, self.navi)

    def test_a_new_file_turns_up(self):
        touch(os.path.join(self.root, "Sets", "Burn.m3u"))
        self.assertEqual([s.name for s in S.listed(self.root)
                          if s.kind == S.COLLECTION], ["Peak", "Burn"])

    def test_can_be_turned_off(self):
        settings.set("gig_search_collection", False)
        self.assertEqual([s.kind for s in S.listed(self.root)], [S.SORTERO])

    def test_a_watched_folder_inside_wins(self):
        S.watch(os.path.join(self.root, "_Inbox"))
        self.assertEqual([s.kind for s in S.listed(self.root)], [S.SORTERO, S.WATCHED])

    def test_same_names_told_apart(self):
        touch(os.path.join(self.root, "_Inbox", "navidrome", "Peak.m3u"))
        touch(os.path.join(self.root, "Sets", "Peak.m3u8"))
        names = [s.name for s in S.listed(self.root) if s.kind == S.COLLECTION]
        self.assertEqual(names, ["Peak (navidrome, .m3u)", "Peak (navidrome, .m3u8)",
                                 "Peak (Sets)"])

    def test_one_playlist_saved_twice_in_one_folder(self):
        touch(os.path.join(self.root, "_Inbox", "navidrome", "Peak.m3u"))
        names = [s.name for s in S.listed(self.root) if s.kind == S.COLLECTION]
        self.assertEqual(names, ["Peak (.m3u)", "Peak (.m3u8)"])

    def test_tracks_read_like_any_playlist_file(self):
        a = os.path.join(self.root, "Techno", "a.mp3")
        with open(self.navi, "w") as fh:
            fh.write("#EXTM3U\n../../Techno/a.mp3\n")
        self.assertEqual(S.tracks(S.listed(self.root)[1]), [a])


class Downstream(unittest.TestCase):
    sets = [("Warmup", ["/m/a.mp3", "/m/b.mp3"]), ("Peak", ["/m/b.mp3", "/m/c.mp3"])]

    def test_each_track_checked_once(self):
        self.assertEqual(convert.playlist_paths(self.sets),
                         ["/m/a.mp3", "/m/b.mp3", "/m/c.mp3"])

    def test_stick_sets_use_the_playable_copies(self):
        class It:
            def __init__(self, src, playable):
                self.src, self.playable = src, playable
        items = [It("/m/a.mp3", "/m/a.mp3"), It("/m/b.mp3", "/lib/b.mp3"),
                 It("/m/c.mp3", None)]
        sets, missing = backup.sets_from(items, self.sets)
        self.assertEqual(sets, [("Warmup", ["/m/a.mp3", "/lib/b.mp3"]),
                                ("Peak", ["/lib/b.mp3"])])
        self.assertEqual(missing, [("Peak", "/m/c.mp3")])


if __name__ == "__main__":
    unittest.main()
