"""Deck profiles: what each player accepts, and what every player accepts."""
import unittest

from sortero.gig import profiles as P
from sortero.gig.profiles import AudioInfo, FAIL, WARN

MP3_320 = AudioInfo("mp3", ".mp3", 44100, kbps=320)
AIFF_16 = AudioInfo("aiff", ".aiff", 44100, depth=16)


def levels(problems):
    return [p.level for p in problems]


class Universal(unittest.TestCase):
    u = P.UNIVERSAL

    def test_is_the_default(self):
        self.assertIs(P.get(P.DEFAULT), self.u)
        self.assertIs(P.get("no-such-player"), self.u)

    def test_only_fat_reads_everywhere(self):
        # XDJ-RX has no HFS+; only the newest players read exFAT.
        self.assertEqual(self.u.file_systems, {P.FAT16, P.FAT32})

    def test_formats_every_documented_player_plays(self):
        self.assertEqual(set(self.u.formats), {"mp3", "aac", "wav", "aiff"})
        # CDJ-3000 and OPUS-QUAD list no 32 kHz MP3.
        self.assertEqual(self.u.formats["mp3"].rates, {44100, 48000})
        self.assertEqual(self.u.formats["mp3"].kbps, (32, 320))
        self.assertEqual(self.u.formats["wav"].depths, {16, 24})
        self.assertEqual(self.u.formats["wav"].rates, {44100, 48000})

    def test_takes_the_strictest_partition_folder_and_artwork_limits(self):
        self.assertEqual(self.u.partitions, P.MAY_FAIL)      # CDJ-900 / CDJ-2000
        self.assertEqual(self.u.folder_depth, 8)
        self.assertEqual(self.u.artwork_px, 800)

    def test_names_the_players_it_could_not_check_formats_for(self):
        note = " ".join(self.u.notes)
        for name in ("XDJ-700", "XDJ-1000", "XDJ-RX", "XDJ-RX2", "XDJ-XZ", "XDJ-RR",
                     "CDJ-3000X"):
            self.assertIn(name, note)
        self.assertNotIn("CDJ-2000NXS2", note)


class ConversionTargets(unittest.TestCase):
    """What gig prep converts to must play on every player with a format table."""

    def test_targets_play_everywhere_known(self):
        for p in P.PROFILES.values():
            if not p.formats_known:
                continue
            for info in (MP3_320, AIFF_16):
                with self.subTest(player=p.name, format=info.format):
                    self.assertEqual(P.check_audio(p, info), [])


class CheckAudio(unittest.TestCase):
    def test_flac_is_newer_players_only(self):
        flac = AudioInfo("flac", ".flac", 44100, depth=16)
        self.assertEqual(levels(P.check_audio(P.get("cdj-2000"), flac)), [FAIL])
        self.assertEqual(P.check_audio(P.get("cdj-2000nxs2"), flac), [])
        self.assertEqual(levels(P.check_audio(P.UNIVERSAL, flac)), [FAIL])

    def test_high_sample_rates(self):
        wav96 = AudioInfo("wav", ".wav", 96000, depth=24)
        self.assertEqual(P.check_audio(P.get("cdj-3000"), wav96), [])
        problems = P.check_audio(P.get("cdj-2000"), wav96)
        self.assertEqual(levels(problems), [FAIL])
        self.assertIn("96 kHz", problems[0].text)
        self.assertIn("44.1 kHz or 48 kHz", problems[0].text)

    def test_32_bit_float_wav(self):
        wav32 = AudioInfo("wav", ".wav", 44100, depth=32)
        self.assertEqual(levels(P.check_audio(P.UNIVERSAL, wav32)), [FAIL])

    def test_32_khz_mp3_plays_on_old_players_only(self):
        m = AudioInfo("mp3", ".mp3", 32000, kbps=128)
        self.assertEqual(P.check_audio(P.get("cdj-2000"), m), [])
        self.assertEqual(levels(P.check_audio(P.get("cdj-3000"), m)), [FAIL])
        self.assertEqual(levels(P.check_audio(P.UNIVERSAL, m)), [FAIL])

    def test_apple_lossless_is_missing_from_the_xdj_rx3(self):
        alac = AudioInfo("alac", ".m4a", 44100, depth=16)
        self.assertEqual(levels(P.check_audio(P.get("xdj-rx3"), alac)), [FAIL])
        self.assertEqual(P.check_audio(P.get("opus-quad"), alac), [])

    def test_only_aac_lc(self):
        he = AudioInfo("aac", ".m4a", 44100, kbps=64, aac_profile="HE-AAC")
        self.assertEqual(levels(P.check_audio(P.UNIVERSAL, he)), [FAIL])
        lc = AudioInfo("aac", ".m4a", 44100, kbps=256, aac_profile="LC")
        self.assertEqual(P.check_audio(P.UNIVERSAL, lc), [])

    def test_extension_must_match(self):
        wrong = AudioInfo("aac", ".mp3", 44100, kbps=256)
        self.assertEqual(levels(P.check_audio(P.UNIVERSAL, wrong)), [FAIL])

    def test_unknown_formats(self):
        ogg = AudioInfo("vorbis", ".ogg", 44100, kbps=192)
        problems = P.check_audio(P.UNIVERSAL, ogg)
        self.assertEqual(levels(problems), [FAIL])
        self.assertIn("VORBIS", problems[0].text)

    def test_player_without_a_format_table_only_warns(self):
        self.assertEqual(levels(P.check_audio(P.get("xdj-rx"), MP3_320)), [WARN])


class CheckVolume(unittest.TestCase):
    def test_fat32_mbr_single_partition_is_clean(self):
        for p in P.PROFILES.values():
            with self.subTest(player=p.name):
                self.assertEqual(P.check_volume(p, P.FAT32, 1, "MBR"), [])

    def test_exfat_fails_for_any_player_but_reads_on_the_newest(self):
        self.assertEqual(levels(P.check_volume(P.UNIVERSAL, P.EXFAT)), [FAIL])
        self.assertEqual(levels(P.check_volume(P.get("cdj-3000"), P.EXFAT)), [FAIL])
        self.assertEqual(P.check_volume(P.get("opus-quad"), P.EXFAT), [])

    def test_hfs_fails_on_the_xdj_rx(self):
        self.assertEqual(levels(P.check_volume(P.get("xdj-rx"), P.HFS)), [FAIL])
        self.assertEqual(P.check_volume(P.get("cdj-2000"), P.HFS), [])

    def test_ntfs_reads_nowhere(self):
        for p in P.PROFILES.values():
            self.assertEqual(levels(P.check_volume(p, P.NTFS)), [FAIL])

    def test_gpt_is_a_warning_not_a_failure(self):
        self.assertEqual(levels(P.check_volume(P.UNIVERSAL, P.FAT32, 1, "GPT")), [WARN])

    def test_extra_partitions(self):
        self.assertEqual(levels(P.check_volume(P.UNIVERSAL, P.FAT32, 2)), [FAIL])
        self.assertEqual(levels(P.check_volume(P.get("cdj-2000nxs2"), P.FAT32, 2)), [WARN])
        self.assertEqual(levels(P.check_volume(P.get("xdj-rr"), P.FAT32, 2)), [WARN])


class Intersect(unittest.TestCase):
    def player(self, key, **formats):
        return P.DeckProfile(key, key, frozenset({P.FAT32}), formats or None)

    def test_bitrate_ranges_narrow_and_disjoint_formats_drop(self):
        a = self.player("a", mp3=P.Format(frozenset({".mp3"}), P.R_44_48, kbps=(32, 320)),
                        aac=P.Format(frozenset({".m4a"}), frozenset({44100}), kbps=(16, 320)))
        b = self.player("b", mp3=P.Format(frozenset({".mp3"}), P.R_32_48, kbps=(64, 256)),
                        aac=P.Format(frozenset({".m4a"}), frozenset({48000}), kbps=(16, 320)))
        both = P.intersect([a, b])
        self.assertEqual(set(both.formats), {"mp3"})
        self.assertEqual(both.formats["mp3"].kbps, (64, 256))
        self.assertEqual(both.formats["mp3"].rates, {44100, 48000})

    def test_players_without_formats_dont_empty_the_list(self):
        a = self.player("a", mp3=P.mp3())
        both = P.intersect([a, self.player("b")])
        self.assertEqual(set(both.formats), {"mp3"})
        self.assertIn("b", both.notes[0])


class Sources(unittest.TestCase):
    def test_every_player_cites_where_its_limits_came_from(self):
        for p in P.PLAYERS:
            with self.subTest(player=p.name):
                self.assertTrue(p.sources)
                for s in p.sources:
                    self.assertTrue(s.startswith("https://"), s)


if __name__ == "__main__":
    unittest.main()
