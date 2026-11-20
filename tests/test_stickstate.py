"""What's on a stick before Gig prep writes it, and replacing a library Sortero didn't write."""
import os, unicodedata

from sortero.gig import backup, disks, enginelib as E, pdb, profiles as P
from sortero.gig import stickstate as S, usbexport as U
from sortero.gig.disks import Volume
from tests.audio import write_wav
from tests.test_tagio import TempDir
from tests.test_usbexport import gig_xml

GOOD = Volume("/stick", P.FAT32, disks.MBR, 1)
EXFAT = Volume("/stick", disks.EXFAT, disks.MBR, 1)


class StickState(TempDir):
    def setUp(self):
        super().setUp()
        self.stick = self.path("GIG")
        os.makedirs(self.stick)
        music = self.path("music")
        os.makedirs(music)
        self.files = [write_wav(os.path.join(music, f"{n}.wav"), seconds=0.5)
                      for n in ("Opener", "Banger", "Closer")]
        self.xml = gig_xml(self.path("gig.xml"), [
            (1, self.files[0], "Opener", "Alpha", ""),
            (2, self.files[1], "Banger", "Beta", ""),
            (3, self.files[2], "Closer", "Alpha", "")],
            [("01 Warmup", [1, 2]), ("02 Peak", [3, 2])])

    def read(self, vol=GOOD):
        return S.read(self.stick, P.EVERY, vol=vol)

    def write(self):
        U.write(self.stick, self.xml, log=lambda *a: None)

    def theirs(self):
        """A library Sortero didn't write: its own, with the marker gone, as
        rekordbox or Engine DJ exporting to the stick would leave it."""
        self.write()
        os.remove(os.path.join(self.stick, U.MARKER))

    def other(self, rel="notes/setlist.txt", data=b"setlist ideas"):
        path = os.path.join(self.stick, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_an_empty_stick(self):
        s = self.read()
        self.assertTrue(s.empty)
        self.assertIn("It's empty.", s.lines())
        self.assertEqual(s.main_plan(), "Writing adds tonight's set.")
        self.assertFalse(s.foreign())

    def test_a_stick_the_players_cant_read_needs_formatting(self):
        s = self.read(EXFAT)
        self.assertTrue(s.needs_format)
        self.assertIn("Format it first", s.main_plan())
        self.assertIn("Format it first", s.backup_plan())

    def test_sorteros_own_set_says_when_and_what(self):
        self.write()
        self.other()
        s = self.read()
        self.assertFalse(s.foreign())
        self.assertEqual([lib.app for lib in s.libraries], [S.REKORDBOX, S.ENGINE])
        self.assertTrue(all(lib.ours for lib in s.libraries))
        said = " ".join(s.lines())
        self.assertIn("Sortero wrote a set here on ", said)
        self.assertIn("for rekordbox and Engine DJ: 2 playlists (“01 Warmup”, “02 Peak”), "
                      "3 tracks.", said)
        self.assertIn("1 other file", said)          # the notes, not the set's music
        plan = s.main_plan()
        self.assertIn("replaces Sortero's playlists with tonight's", plan)
        self.assertIn("1 other file stays.", plan)

    def test_accents_either_way_are_still_the_libraries_music(self):
        self.write()
        mark = U._marker(self.stick)
        # The same name in the other Unicode form, as macOS sometimes stores it
        nfd = unicodedata.normalize("NFD", "Contents/Ålpha/x.wav")
        self.other(nfd, b"x")
        mark["audio"].append(unicodedata.normalize("NFC", nfd))
        U._put(self.stick, U.MARKER.replace(os.sep, "/"), __import__("json").dumps(
            mark).encode())
        self.assertEqual(self.read().other_files, 0)

    def test_a_library_sortero_didnt_write_can_be_replaced(self):
        self.theirs()
        notes = self.other()
        s = self.read()
        theirs = s.foreign()
        self.assertEqual([lib.app for lib in theirs], [S.REKORDBOX, S.ENGINE])
        self.assertIn("A rekordbox library Sortero didn't write: 2 playlists", " ".join(s.lines()))
        plan = s.main_plan()
        self.assertIn("Sortero replaces the rekordbox library and the Engine DJ library", plan)
        self.assertIn("the 3 music files it put on the stick", plan)
        self.assertIn("Everything else on the stick stays.", plan)
        self.assertIn("export from rekordbox yourself, which adds to it", plan)

        logged = []
        self.assertEqual(S.replace(s, log=logged.append), 3)
        self.assertIsNone(U.foreign(self.stick))
        self.assertFalse(os.path.exists(os.path.join(self.stick, pdb.RELATIVE)))
        self.assertFalse(os.path.exists(os.path.join(self.stick, E.FOLDER)))
        self.assertFalse(os.path.exists(os.path.join(self.stick, U.CONTENTS, "Alpha")))
        self.assertTrue(os.path.isfile(notes))
        self.assertTrue(self.read().empty is False)     # the notes are still there
        self.write()                                    # and now it writes
        self.assertTrue(all(lib.ours for lib in self.read().libraries))

    def test_for_pioneer_players_only_an_engine_library_is_left_alone(self):
        self.write()
        U.write(self.stick, self.xml, log=lambda *a: None, engine=False)
        os.makedirs(os.path.join(self.stick, os.path.dirname(E.DATABASE)), exist_ok=True)
        with open(os.path.join(self.stick, E.DATABASE), "wb") as fh:
            fh.write(b"Engine DJ's")
        s = self.read()
        self.assertEqual([lib.app for lib in s.foreign()], [S.ENGINE])
        self.assertFalse(s.foreign(engine=False))
        S.replace(s, engine=False, log=lambda *a: None)
        with open(os.path.join(self.stick, E.DATABASE), "rb") as fh:
            self.assertEqual(fh.read(), b"Engine DJ's")

    def test_the_folders_backup_is_never_replaced(self):
        src = self.files[0]
        p = backup.plan(self.stick, [("Warmup", [src])])
        backup.run(p, log=lambda *a: None)
        self.theirs()
        s = self.read()
        self.assertEqual(s.backup_files, 1)
        self.assertIn("Sortero's folders backup from ", " ".join(s.lines()))
        S.replace(s, log=lambda *a: None)
        self.assertEqual(self.read().backup_files, 1)
        self.assertIn("Nothing else on the stick is touched", self.read().backup_plan())
