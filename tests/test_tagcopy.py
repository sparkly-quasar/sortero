"""MusicBrainz and AcoustID tags land in an MP3 under MusicBrainz Picard's names."""
import unittest

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3
from mutagen.mp4 import MP4FreeForm

from sortero.gig import tagcopy
from tests.test_tagio import TempDir

RECORDING = "8f3471b5-7e6a-48da-86a9-c1c07a0f47ae"
VORBIS = {
    "TITLE": ["Quasar"], "ARTIST": ["Daryn"],
    "MUSICBRAINZ_TRACKID": [RECORDING],
    "MUSICBRAINZ_RELEASETRACKID": ["c1d2e3f4-0000-4000-8000-000000000001"],
    "MUSICBRAINZ_ALBUMID": ["a1b2c3d4-0000-4000-8000-000000000002"],
    "MUSICBRAINZ_ARTISTID": ["a1b2c3d4-0000-4000-8000-000000000003"],
    "MUSICBRAINZ_ALBUMARTISTID": ["a1b2c3d4-0000-4000-8000-000000000004"],
    "MUSICBRAINZ_RELEASEGROUPID": ["a1b2c3d4-0000-4000-8000-000000000005"],
    "ACOUSTID_ID": ["a1b2c3d4-0000-4000-8000-000000000006"],
    "RELEASETYPE": ["single"], "RELEASESTATUS": ["official"], "RELEASECOUNTRY": ["GB"],
    "BARCODE": ["5056167112345"],
}


class PicardNames(TempDir):
    def write(self, frames, name="out.mp3"):
        dst = self.path(name)
        open(dst, "wb").close()
        tagcopy.write(dst, frames)
        return dst

    def test_vorbis_ids_become_picards_id3_frames(self):
        frames, _ = tagcopy._from_vorbis(VORBIS, [])
        t = ID3(self.write(frames))
        # the recording id lives in a UFID frame, as Picard writes it
        self.assertEqual(t["UFID:http://musicbrainz.org"].data, RECORDING.encode())
        self.assertEqual(str(t["TXXX:MusicBrainz Release Track Id"]),
                         VORBIS["MUSICBRAINZ_RELEASETRACKID"][0])
        self.assertEqual(str(t["TXXX:MusicBrainz Album Id"]), VORBIS["MUSICBRAINZ_ALBUMID"][0])
        self.assertEqual(str(t["TXXX:Acoustid Id"]), VORBIS["ACOUSTID_ID"][0])
        self.assertEqual(str(t["TXXX:MusicBrainz Album Type"]), "single")
        self.assertEqual(str(t["TXXX:BARCODE"]), "5056167112345")   # already Picard's name
        # nothing left under the Vorbis spelling
        self.assertFalse([k for k in t.keys() if k.startswith("TXXX:MUSICBRAINZ")])

    def test_readers_that_know_musicbrainz_find_them(self):
        """mutagen's EasyID3 has its own MusicBrainz name table: a second opinion."""
        frames, _ = tagcopy._from_vorbis(VORBIS, [])
        e = EasyID3(self.write(frames))
        self.assertEqual(e["musicbrainz_trackid"], [RECORDING])
        self.assertEqual(e["musicbrainz_albumid"], VORBIS["MUSICBRAINZ_ALBUMID"])
        self.assertEqual(e["musicbrainz_artistid"], VORBIS["MUSICBRAINZ_ARTISTID"])
        self.assertEqual(e["musicbrainz_albumartistid"], VORBIS["MUSICBRAINZ_ALBUMARTISTID"])
        self.assertEqual(e["musicbrainz_releasegroupid"], VORBIS["MUSICBRAINZ_RELEASEGROUPID"])
        self.assertEqual(e["acoustid_id"], VORBIS["ACOUSTID_ID"])
        self.assertEqual(e["releasecountry"], ["GB"])
        self.assertEqual(e["musicbrainz_albumtype"], ["single"])
        self.assertEqual(e["musicbrainz_albumstatus"], ["official"])

    def test_apple_lossless_track_id_becomes_the_ufid(self):
        tags = {"\xa9nam": ["Song"],
                "----:com.apple.iTunes:MusicBrainz Track Id": [MP4FreeForm(RECORDING.encode())],
                "----:com.apple.iTunes:MusicBrainz Album Id": [MP4FreeForm(b"album-id")]}
        frames, _ = tagcopy._from_mp4(tags)
        t = ID3(self.write(frames))
        self.assertEqual(t["UFID:http://musicbrainz.org"].data, RECORDING.encode())
        self.assertEqual(str(t["TXXX:MusicBrainz Album Id"]), "album-id")
        self.assertNotIn("TXXX:MusicBrainz Track Id", t)

    def test_id3_sources_keep_their_ufid(self):
        frames, _ = tagcopy._from_vorbis(VORBIS, [])
        again, _ = tagcopy.read(self.write(frames))      # an MP3 or AIFF source
        t = ID3(self.write(again, "again.mp3"))
        self.assertEqual(t["UFID:http://musicbrainz.org"].data, RECORDING.encode())


if __name__ == "__main__":
    unittest.main()
