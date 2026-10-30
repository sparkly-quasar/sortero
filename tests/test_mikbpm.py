"""A BPM Mixed In Key kept only in its beat grid is shown, and can be written as a tag."""
import base64, json, os, shutil, subprocess, unittest
from unittest import mock

from mutagen.id3 import ID3, GEOB, TBPM, TPE1, TIT2

from sortero import bpmfix, journal, library, paths, session, supporter, tagcache
from sortero.journal import revert
from sortero.tagio import Track
from tests import audio
from tests.test_tagio import TempDir


def grid_blob(tempo):
    """A beat grid as Mixed In Key writes it: base64 JSON wrapped at 72 characters."""
    raw = base64.b64encode(json.dumps({"source": "mixedinkey", "tempo": tempo,
                                       "algorithm": 12, "beats": [0.03, 0.46]}).encode())
    return b"\n".join(raw[i:i + 72] for i in range(0, len(raw), 72)) + b"\n"


def grid_frame(tempo):
    return GEOB(encoding=0, mime="application/json", filename="", desc="BeatGrid",
                data=grid_blob(tempo))


def mp3(path, *frames):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tags = ID3()
    tags.add(TPE1(encoding=3, text=["Chlär"]))
    tags.add(TIT2(encoding=3, text=["Residual Heat"]))
    for f in frames:
        tags.add(f)
    tags.save(path)
    return path


class ReadingTheGrid(TempDir):
    def test_tempo_is_rounded_like_a_bpm_tag(self):
        for tempo, shown in ((140.99906921386719, "141"), (90.0001068, "90"),
                             (93.3333, "93.33"), (112.7989, "112.8")):
            with self.subTest(tempo=tempo):
                p = mp3(self.path(f"{tempo}.mp3"), grid_frame(tempo))
                self.assertEqual(Track(p).mik_bpm(), shown)

    def test_no_grid_or_a_broken_one_is_none(self):
        self.assertIsNone(Track(mp3(self.path("none.mp3"))).mik_bpm())
        bad = GEOB(encoding=0, mime="application/json", filename="", desc="BeatGrid",
                   data=b"not base64 at all!")
        self.assertIsNone(Track(mp3(self.path("bad.mp3"), bad)).mik_bpm())

    def test_aiff_grid_in_the_chunk(self):
        p = audio.write_aiff(self.path("t.aiff"))
        audio.tag_chunk(p, grid_frame(128.0))
        self.assertEqual(Track(p).mik_bpm(), "128")

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg to make a flac")
    def test_flac_grid_comment(self):
        from mutagen.flac import FLAC
        p = self.path("t.flac")
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "sine=d=0.5",
                        p], check=True)
        f = FLAC(p)
        f["beatgrid"] = grid_blob(104.0).decode()
        f.save()
        self.assertEqual(Track(p).mik_bpm(), "104")


class ScanAndWrite(TempDir):
    def setUp(self):
        super().setUp()
        tagcache.clear()
        self.addCleanup(tagcache.clear)
        jdir = self.path("journals")
        os.makedirs(jdir)
        for target, value, to in ((paths, "journals_dir", lambda: jdir),
                                  (journal.paths, "journals_dir", lambda: jdir),
                                  (session, "record", lambda *a: None),
                                  (supporter, "count", lambda *a: None)):
            p = mock.patch.object(target, value, to)
            p.start()
            self.addCleanup(p.stop)
        self.root = self.path("Music")
        self.only_grid = mp3(os.path.join(self.root, "Techno", "a.mp3"), grid_frame(140.999))
        self.tagged = mp3(os.path.join(self.root, "Techno", "b.mp3"), grid_frame(140.999),
                          TBPM(encoding=3, text=["138"]))

    def scan(self):
        return {os.path.basename(r.path): r for r in library.scan(self.root)}

    def test_scan_falls_back_to_the_grid_only_without_a_tag(self):
        recs = self.scan()
        self.assertEqual((recs["a.mp3"].bpm, recs["a.mp3"].bpm_from_mik), ("141", True))
        self.assertEqual((recs["b.mp3"].bpm, recs["b.mp3"].bpm_from_mik), ("138", False))

    def test_write_then_undo(self):
        recs = self.scan()
        path, failed = bpmfix.write_mik(self.root, [recs["a.mp3"]], log=lambda m: None)
        self.assertEqual(failed, 0)
        self.assertEqual(Track(self.only_grid).get("bpm"), "141")
        self.assertFalse(self.scan()["a.mp3"].bpm_from_mik)

        revert(path, log=lambda m: None)
        self.assertIsNone(Track(self.only_grid).get("bpm"))
        self.assertTrue(self.scan()["a.mp3"].bpm_from_mik)


if __name__ == "__main__":
    unittest.main()
