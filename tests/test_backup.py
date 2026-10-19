"""The plain-folder backup stick."""
import os, shutil, subprocess, unittest

from sortero.gig import backup as B, profiles as P, verify as V
from sortero.gig.disks import MBR, Volume
from tests.test_tagio import TempDir

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class Names(unittest.TestCase):
    def test_numbered_in_set_order(self):
        want = B.layout([("Warmup", ["/m/b.mp3", "/m/a.mp3"]), ("Peak", ["/m/c.mp3"])])
        self.assertEqual(list(want), ["01 Warmup/01 b.mp3", "01 Warmup/02 a.mp3",
                                      "02 Peak/01 c.mp3"])

    def test_wider_numbers_for_long_sets(self):
        want = B.layout([("Long", [f"/m/{i}.mp3" for i in range(120)])])
        self.assertEqual(list(want)[0], "01 Long/001 0.mp3")

    def test_fat32_safe(self):
        self.assertEqual(B.fat_name('What? "Edit": Part 1.mp3'), "What- -Edit-- Part 1.mp3")
        self.assertEqual(B.fat_name("Ends in a dot..mp3"), "Ends in a dot.mp3")
        self.assertEqual(B.fat_name("CON.mp3"), "CON_.mp3")
        self.assertEqual(len(B.fat_name("x" * 300 + ".mp3")), 255)

    def test_a_slash_never_makes_a_folder(self):
        # a playlist "House / Techno" made "01 House " (FAT32 drops the space) and "Techno"
        self.assertEqual(B.fat_name("01 House / Techno"), "01 House - Techno")
        self.assertEqual([p.split("/")[0] for p in B.layout([("House / Techno", ["/a.mp3"])])],
                         ["01 House - Techno"])

    def test_accents_are_stored_whole(self):
        import unicodedata
        self.assertEqual(B.fat_name(unicodedata.normalize("NFD", "Holldën.mp3")), "Holldën.mp3")
        for rel in B.layout([("Peak: Hour?", ['/m/What? "Edit".mp3'])]):
            self.assertEqual(V.name_problems(rel), [])


class Stick(TempDir):
    def setUp(self):
        super().setUp()
        self.stick = self.path("GIG")
        os.makedirs(self.stick)
        self.music = self.path("music")
        os.makedirs(self.music)

    def track(self, name, data=None):
        p = os.path.join(self.music, name)
        with open(p, "wb") as fh:
            fh.write(data or name.encode() * 100)
        return p

    def write(self, sets):
        p = B.plan(self.stick, sets)
        return p, B.run(p, log=lambda m: None)

    def on_stick(self, rel):
        return os.path.join(self.stick, *rel.split("/"))

    def test_writes_folders_and_a_manifest(self):
        a, b = self.track("A.mp3"), self.track("B.mp3")
        _, res = self.write([("Warmup", [a, b])])
        self.assertEqual(res.written, ["01 Warmup/01 A.mp3", "01 Warmup/02 B.mp3"])
        with open(self.on_stick("01 Warmup/02 B.mp3"), "rb") as fh:
            self.assertEqual(fh.read(), b"B.mp3" * 100)
        self.assertEqual(B.sources(self.stick), {"01 Warmup/01 A.mp3": a,
                                                 "01 Warmup/02 B.mp3": b})
        leftovers = [f for _, _, fs in os.walk(self.stick) for f in fs if f.startswith(B.TMP)]
        self.assertEqual(leftovers, [])

    def test_second_run_leaves_unchanged_files_alone(self):
        a = self.track("A.mp3")
        self.write([("Warmup", [a])])
        stamp = os.stat(self.on_stick("01 Warmup/01 A.mp3")).st_mtime_ns
        p, res = self.write([("Warmup", [a])])
        self.assertEqual((p.copy, res.unchanged), ([], ["01 Warmup/01 A.mp3"]))
        self.assertEqual(os.stat(self.on_stick("01 Warmup/01 A.mp3")).st_mtime_ns, stamp)

    def test_changed_original_is_copied_again(self):
        a = self.track("A.mp3")
        self.write([("Warmup", [a])])
        self.track("A.mp3", b"new audio" * 50)
        _, res = self.write([("Warmup", [a])])
        self.assertEqual(res.written, ["01 Warmup/01 A.mp3"])

    def test_new_set_order_replaces_the_old_one(self):
        a, b = self.track("A.mp3"), self.track("B.mp3")
        self.write([("Warmup", [a]), ("Peak", [b])])
        _, res = self.write([("Peak", [b])])
        self.assertEqual(res.written, ["01 Peak/01 B.mp3"])
        self.assertEqual(sorted(res.removed), ["01 Warmup/01 A.mp3", "02 Peak/01 B.mp3"])
        self.assertEqual(sorted(os.listdir(self.stick)), [B.MANIFEST, "01 Peak"])

    def test_files_sortero_didnt_write_are_never_touched(self):
        os.makedirs(self.on_stick("01 Warmup"))
        mine = self.on_stick("01 Warmup/01 A.mp3")
        with open(mine, "wb") as fh:
            fh.write(b"my own file")
        with open(self.on_stick("notes.txt"), "w") as fh:
            fh.write("set notes")
        _, res = self.write([("Warmup", [self.track("A.mp3")])])
        self.assertEqual(res.skipped[0][0], "01 Warmup/01 A.mp3")
        self.assertIn("didn't put there", res.skipped[0][1])
        with open(mine, "rb") as fh:
            self.assertEqual(fh.read(), b"my own file")
        _, res = self.write([])
        self.assertTrue(os.path.exists(self.on_stick("notes.txt")))
        self.assertTrue(os.path.exists(mine))

    def test_not_enough_space(self):
        p = B.plan(self.stick, [("Warmup", [self.track("A.mp3")])])
        real = B.free_space
        B.free_space = lambda path: 10
        try:
            with self.assertRaises(B.BackupError):
                B.run(p, log=lambda m: None)
        finally:
            B.free_space = real
        self.assertFalse(os.path.exists(self.on_stick("01 Warmup")))

    def test_missing_original_is_skipped(self):
        p, res = self.write([("Warmup", [os.path.join(self.music, "gone.mp3")])])
        self.assertIn("isn't there", res.skipped[0][1])

    def test_appledouble_files_are_cleared_but_not_lookalikes(self):
        os.makedirs(self.on_stick("Old"))
        with open(self.on_stick("Old/._Track.mp3"), "wb") as fh:
            fh.write(B.APPLEDOUBLE_MAGIC + b"\0" * 60)
        with open(self.on_stick("._not metadata.mp3"), "wb") as fh:
            fh.write(b"ID3 a real file someone named oddly")
        _, res = self.write([("Warmup", [self.track("A.mp3")])])
        self.assertEqual(res.appledouble, 1)
        self.assertFalse(os.path.exists(self.on_stick("Old/._Track.mp3")))
        self.assertTrue(os.path.exists(self.on_stick("._not metadata.mp3")))

    def test_interrupted_run_leftovers_are_cleared(self):
        os.makedirs(self.on_stick("01 Warmup"))
        with open(self.on_stick(f"01 Warmup/{B.TMP}01 A.mp3"), "wb") as fh:
            fh.write(b"half")
        self.write([("Warmup", [self.track("A.mp3")])])
        self.assertEqual(sorted(os.listdir(self.on_stick("01 Warmup"))), ["01 A.mp3"])

    @unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
    def test_stick_check_compares_every_file_with_its_original(self):
        src = os.path.join(self.music, "Real.mp3")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1",
                        "-c:a", "libmp3lame", "-b:a", "320k", src], check=True)
        self.write([("Warmup", [src])])
        vol = Volume(self.stick, P.FAT32, MBR, 1)
        r = V.verify(self.stick, vol=vol)
        self.assertTrue(r.ready, "\n".join(r.lines()))
        sums = next(c for c in r.checks if c.title == "Checksums")
        self.assertEqual(sums.level, V.PASS)
        with open(self.on_stick("01 Warmup/01 Real.mp3"), "r+b") as fh:
            fh.seek(-4, 2)
            fh.write(b"\xff\xff\xff\xff")
        sums = next(c for c in V.verify(self.stick, vol=vol, decode=False).checks
                    if c.title == "Checksums")
        self.assertEqual(sums.level, V.FAIL)


if __name__ == "__main__":
    unittest.main()
