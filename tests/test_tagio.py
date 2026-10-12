"""Reading and writing tags without damaging the file."""
import os, shutil, subprocess, tempfile, unittest

from mutagen.id3 import TKEY, TPE1, TIT2, TXXX

from sortero.tagio import Track
from tests import audio

MAGIC = {".aiff": b"FORM", ".wav": b"RIFF"}
WRITERS = {".aiff": audio.write_aiff, ".wav": audio.write_wav}


class TempDir(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sortero-test-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.dir, name)


class AiffWavTags(TempDir):
    """AIFF and WAV keep ID3 in a chunk; Sortero must read and write it there."""

    def each(self):
        for ext, write in WRITERS.items():
            with self.subTest(ext=ext):
                yield ext, write(self.path("t" + ext))

    def test_reads_tags_from_the_chunk(self):
        for ext, p in self.each():
            audio.tag_chunk(p, TKEY(encoding=3, text=["5A"]),
                            TPE1(encoding=3, text=["Chunk Artist"]),
                            TXXX(encoding=3, desc="EnergyLevel", text=["6"]))
            t = Track(p)
            self.assertEqual(t.get("key"), "5A")
            self.assertEqual(t.get("artist"), "Chunk Artist")
            self.assertEqual(t.get("energylevel"), "6")
            self.assertGreater(t.length, 0)

    def test_writing_keeps_the_header_first(self):
        for ext, p in self.each():
            audio.tag_chunk(p, TPE1(encoding=3, text=["Chunk Artist"]))
            before = audio.chunk(p, b"SSND" if ext == ".aiff" else b"data")
            t = Track(p)
            t.set("key", "8A")
            t.set("genre", "Techno")
            self.assertTrue(t.save())

            self.assertEqual(audio.read(p)[:4], MAGIC[ext])
            self.assertEqual(Track(p).stray_id3, 0)
            again = Track(p)
            self.assertEqual(again.get("key"), "8A")
            self.assertEqual(again.get("genre"), "Techno")
            self.assertEqual(again.get("artist"), "Chunk Artist")   # untouched
            self.assertEqual(audio.chunk(p, b"SSND" if ext == ".aiff" else b"data"),
                             before)                                 # audio untouched

    def test_untagged_file_gets_a_chunk_not_a_prefix(self):
        for ext, p in self.each():
            t = Track(p)
            t.set("title", "Strobe")
            self.assertTrue(t.save())
            self.assertEqual(audio.read(p)[:4], MAGIC[ext])
            self.assertEqual(Track(p).get("title"), "Strobe")


class DamagedFiles(TempDir):
    """Files an older Sortero already damaged stay readable until repaired."""

    def test_damaged_file_reads_its_stray_tag(self):
        p = audio.write_aiff(self.path("d.aiff"))
        audio.damage(p, TKEY(encoding=3, text=["8A"]))
        t = Track(p)
        self.assertGreater(t.stray_id3, 0)
        self.assertEqual(t.get("key"), "8A")

    def test_writing_a_damaged_file_adds_no_second_tag(self):
        p = audio.write_wav(self.path("d.wav"))
        audio.damage(p, TKEY(encoding=3, text=["8A"]))
        t = Track(p)
        t.set("genre", "House")
        self.assertTrue(t.save())
        after = Track(p)
        self.assertEqual(after.get("genre"), "House")
        self.assertEqual(audio.read(p)[after.stray_id3:after.stray_id3 + 4], b"RIFF")
        self.assertFalse(audio.read(p)[10:].startswith(b"ID3"))


class OtherFormats(TempDir):
    def test_unreadable_file_is_never_given_an_id3_tag(self):
        p = self.path("broken.m4a")
        with open(p, "wb") as fh:
            fh.write(b"\0" * 64)
        t = Track(p)
        self.assertFalse(t.ok)
        t.set("key", "8A")
        self.assertFalse(t.save())
        self.assertEqual(audio.read(p), b"\0" * 64)

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg to make an mp3")
    def test_mp3_round_trip(self):
        p = self.path("t.mp3")
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "sine=d=0.5",
                        "-b:a", "320k", p], check=True)
        t = Track(p)
        t.set("key", "8A")
        t.set("artist", "Someone")
        self.assertTrue(t.save())
        again = Track(p)
        self.assertEqual((again.get("key"), again.get("artist")), ("8A", "Someone"))
        self.assertGreater(again.length, 0)


if __name__ == "__main__":
    unittest.main()
