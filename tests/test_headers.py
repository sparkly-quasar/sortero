"""Finding AIFF/WAV files with a stray ID3 tag in front, and repairing them."""
import os, unittest

from mutagen.id3 import TCON, TKEY, TPE1, TXXX

from sortero import headers, journal, library
from sortero.journal import Journal
from sortero.tagio import Track
from tests import audio
from tests.test_tagio import TempDir


class Detect(TempDir):
    def test_clean_files_are_not_flagged(self):
        self.assertEqual(headers.stray_id3(audio.write_aiff(self.path("a.aiff"))), 0)
        self.assertEqual(headers.stray_id3(audio.write_wav(self.path("w.wav"))), 0)

    def test_damaged_files_report_where_the_header_starts(self):
        for name, write, magic in (("a.aiff", audio.write_aiff, b"FORM"),
                                   ("w.wav", audio.write_wav, b"RIFF")):
            with self.subTest(name=name):
                p = write(self.path(name))
                audio.damage(p, TKEY(encoding=3, text=["8A"]))
                off = headers.stray_id3(p)
                self.assertGreater(off, 0)
                self.assertEqual(audio.read(p)[off:off + 4], magic)

    def test_mp3_with_its_normal_tag_is_not_flagged(self):
        p = self.path("t.mp3")
        audio.damage(p, TKEY(encoding=3, text=["8A"]))    # an ID3 tag, then nothing
        self.assertEqual(headers.stray_id3(p), 0)

    def test_tag_followed_by_garbage_is_not_flagged(self):
        p = self.path("odd.wav")
        with open(p, "wb") as fh:
            fh.write(b"\0" * 64)
        audio.damage(p, TKEY(encoding=3, text=["8A"]))
        self.assertEqual(headers.stray_id3(p), 0)


class Repair(TempDir):
    def setUp(self):
        super().setUp()
        self.root = self.path("Collection")
        os.makedirs(os.path.join(self.root, "Techno"))

    def damaged(self, name, write):
        """A file rekordbox tagged properly, then an older Sortero damaged."""
        p = write(os.path.join(self.root, "Techno", name))
        audio.tag_chunk(p, TPE1(encoding=3, text=["Chunk Artist"]),
                        TKEY(encoding=3, text=["5A"]),
                        TXXX(encoding=3, desc="EnergyLevel", text=["6"]))
        clean_audio = audio.chunk(p, b"SSND" if name.endswith(".aiff") else b"data")
        audio.damage(p, TKEY(encoding=3, text=["8A"]), TCON(encoding=3, text=["Techno"]))
        return p, clean_audio

    def test_repair_merges_the_stray_tag_into_the_chunk(self):
        for name, write, magic, cid in (("a.aiff", audio.write_aiff, b"FORM", b"SSND"),
                                        ("w.wav", audio.write_wav, b"RIFF", b"data")):
            with self.subTest(name=name):
                p, clean_audio = self.damaged(name, write)
                j = Journal("repair-headers", self.root)
                self.assertTrue(headers.repair_one(self.root, p, j))

                self.assertEqual(audio.read(p)[:4], magic)
                self.assertEqual(headers.stray_id3(p), 0)
                self.assertEqual(audio.chunk(p, cid), clean_audio)
                t = Track(p)
                self.assertEqual(t.get("key"), "8A")            # Sortero's later edit wins
                self.assertEqual(t.get("genre"), "Techno")      # Sortero's addition kept
                self.assertEqual(t.get("artist"), "Chunk Artist")   # rekordbox's kept
                self.assertEqual(t.get("energylevel"), "6")     # MIK's kept
                self.assertGreater(t.length, 0)

    def test_original_is_quarantined_and_undo_restores_it_exactly(self):
        p, _ = self.damaged("a.aiff", audio.write_aiff)
        original = audio.read(p)
        jpath, n, failed = headers.repair(self.root, [p], log=lambda m: None)
        self.assertEqual((n, failed), (1, 0))

        qdir = os.path.join(self.root, "_Quarantine", headers.QUARANTINE_SUB)
        [kept] = os.listdir(qdir)
        self.assertEqual(audio.read(os.path.join(qdir, kept)), original)

        journal.revert(jpath, log=lambda m: None)
        self.assertEqual(audio.read(p), original)
        self.assertFalse(os.path.exists(os.path.join(qdir, kept)))

    def test_clean_file_is_left_alone(self):
        p = audio.write_wav(os.path.join(self.root, "Techno", "clean.wav"))
        before = audio.read(p)
        jpath, n, failed = headers.repair(self.root, [p], log=lambda m: None)
        self.assertEqual((jpath, n, failed), (None, 0, 0))
        self.assertEqual(audio.read(p), before)

    def test_no_temporary_files_are_left_behind(self):
        p, _ = self.damaged("w.wav", audio.write_wav)
        headers.repair(self.root, [p], log=lambda m: None)
        left = [f for f in os.listdir(os.path.dirname(p)) if f.startswith(".sortero")]
        self.assertEqual(left, [])

    def test_library_health_lists_damaged_files_until_repaired(self):
        p, _ = self.damaged("a.aiff", audio.write_aiff)
        audio.write_wav(os.path.join(self.root, "Techno", "clean.wav"))
        recs = library.scan(self.root)
        self.assertEqual([r.path for r in library.health(recs)["stray_id3"]], [p])

        headers.repair(self.root, [p], log=lambda m: None)
        recs = library.scan(self.root)
        self.assertEqual(library.health(recs)["stray_id3"], [])   # quarantined copy ignored
        [r] = [r for r in recs if r.path == p]
        self.assertEqual((r.artist, r.key), ("Chunk Artist", "8A"))


if __name__ == "__main__":
    unittest.main()
