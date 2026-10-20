"""Checking a stick before a gig, using a real rekordbox export."""
import contextlib, io, os, shutil, subprocess, unittest
from unittest import mock

from mutagen.id3 import ID3, TIT2

from sortero.gig import disks, pdb, profiles as P, verify as V
from sortero.gig.disks import Volume
from tests.test_tagio import TempDir

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "rekordcrate-demo", "export.pdb")
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg and ffprobe")
GOOD = Volume("/stick", P.FAT32, disks.MBR, 1)


class Export(unittest.TestCase):
    def test_reads_the_tracks_rekordbox_exported(self):
        e = pdb.read(FIXTURE)
        self.assertEqual([(t.path, t.size) for t in e.tracks], [
            ("/Contents/Loopmasters/UnknownAlbum/Demo Track 1.mp3", 6899624),
            ("/Contents/Loopmasters/UnknownAlbum/Demo Track 2.mp3", 5124342)])

    def test_rows_rekordbox_deleted_are_skipped(self):
        # The file still holds HORN.wav, SIREN.wav and others in rows marked
        # deleted; a player doesn't list them, so neither does the check.
        with open(FIXTURE, "rb") as fh:
            self.assertIn(b"HORN.wav", fh.read())
        self.assertNotIn("HORN", " ".join(t.path for t in pdb.read(FIXTURE).tracks))

    def test_damaged_export_is_an_error(self):
        with self.assertRaises(pdb.ExportError):
            with open(FIXTURE, "rb") as fh:
                data = fh.read()
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdb", delete=False) as t:
                t.write(data[:5000])
            self.addCleanup(os.remove, t.name)
            pdb.read(t.name)


def mp3(path, size=None, seconds=1):
    """A real MP3, padded to an exact size when asked."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", "-ac", "2", "-c:a", "libmp3lame",
                    "-b:a", "320k", path], check=True)
    if size:
        tags = ID3()
        tags.add(TIT2(encoding=3, text=["x"]))
        tags.save(path, padding=lambda info: 0)
        tags.save(path, padding=lambda info: size - os.path.getsize(path))
        assert os.path.getsize(path) == size
    return path


@needs_ffmpeg
class Stick(TempDir):
    def setUp(self):
        super().setUp()
        self.stick = self.path("GIG")
        os.makedirs(os.path.join(self.stick, "PIONEER", "rekordbox"))
        shutil.copy(FIXTURE, os.path.join(self.stick, pdb.RELATIVE))
        # rekordbox 7.2.11+ writes OneLibrary beside it. It's encrypted, so its
        # contents are never read: being there is what's checked.
        self.one_library = os.path.join(self.stick, "PIONEER", "rekordbox", "exportLibrary.db")
        with open(self.one_library, "wb") as fh:
            fh.write(b"\0" * 64)
        for t in pdb.read(FIXTURE).tracks:
            mp3(pdb.on_stick(self.stick, t.path), t.size)

    def run_check(self, profile=P.UNIVERSAL, vol=GOOD, **kw):
        return V.verify(self.stick, profile, vol=vol, **kw)

    def level(self, report, title):
        return next(c for c in report.checks if c.title == title).level

    def check(self, report, title):
        return next(c for c in report.checks if c.title == title)

    def test_a_good_stick_is_gig_ready(self):
        r = self.run_check()
        self.assertTrue(r.ready, "\n".join(r.lines()))
        self.assertEqual(r.verdict, "GIG READY")
        self.assertEqual([c.level for c in r.checks], [V.PASS] * 5)
        self.assertIn("2 tracks, all on the stick", self.check(r, "rekordbox").summary)
        self.assertIn("both Device Library and OneLibrary", self.check(r, "rekordbox").summary)

    def test_for_pioneer_and_denon_without_an_engine_library(self):
        r = self.run_check(P.EVERY)
        self.assertTrue(r.ready, "\n".join(r.lines()))
        c = self.check(r, "Engine DJ")
        self.assertEqual(c.level, V.PASS)
        self.assertIn("Denon players will browse it as folders", c.summary)
        self.assertEqual(r.lines()[0], f"Checking {self.stick} for every Pioneer or Denon player")

    def test_an_engine_library_is_noticed(self):
        os.makedirs(os.path.join(self.stick, V.ENGINE_DIR))
        c = self.check(self.run_check(P.EVERY), "Engine DJ")
        self.assertEqual(c.level, V.PASS)
        self.assertIn("an Engine library is there", c.summary)

    def test_denon_only_doesnt_need_rekordbox_libraries(self):
        os.remove(self.one_library)
        r = self.run_check(P.ANY_DENON)
        self.assertEqual(self.level(r, "rekordbox"), V.PASS)    # export there, still checked
        self.assertNotIn("Pioneer", self.check(r, "Engine DJ").summary)
        shutil.rmtree(os.path.join(self.stick, "PIONEER"))
        titles = [c.title for c in self.run_check(P.ANY_DENON).checks]
        self.assertNotIn("rekordbox", titles)
        self.assertIn("Engine DJ", titles)
        # and a Pioneer-only check doesn't ask about Engine
        self.assertNotIn("Engine DJ", [c.title for c in self.run_check().checks])

    def main(self, profile=P.UNIVERSAL, playlists=None):
        r = self.run_check(profile, role=V.MAIN, playlists=playlists, decode=False)
        return r, self.check(r, "Main stick")

    def test_main_stick_with_a_rekordbox_library(self):
        r, c = self.main()
        self.assertEqual(c.level, V.PASS)
        self.assertIn("rekordbox library is there", c.summary)
        self.assertEqual(r.libraries, ["rekordbox"])
        self.assertEqual(r.audio, 2)

    def test_main_stick_for_both_makers_wants_either_library(self):
        r, c = self.main(P.EVERY)
        self.assertEqual(c.level, V.WARN)
        self.assertIn("no Engine DJ library, so Denon players will only browse", c.summary)
        self.assertNotIn("Engine DJ", [x.title for x in r.checks])   # said once, here
        os.makedirs(os.path.join(self.stick, V.ENGINE_DIR))
        r, c = self.main(P.EVERY)
        self.assertEqual(c.level, V.PASS)
        self.assertIn("rekordbox and Engine DJ libraries are there", c.summary)
        self.assertEqual(r.libraries, ["rekordbox", "Engine DJ"])

    def test_main_stick_without_a_library_isnt_ready(self):
        shutil.rmtree(os.path.join(self.stick, "PIONEER"))
        r, c = self.main(P.EVERY)
        self.assertEqual(c.level, V.FAIL)
        self.assertIn("no rekordbox or Engine DJ library on it yet", c.summary)
        self.assertNotIn("rekordbox", [x.title for x in r.checks])
        self.assertEqual(r.calm_verdict, "Not ready yet: 1 thing to fix.")
        self.assertTrue(r.first_problem.startswith("Main stick: there's no"))
        # an Engine library is no use to Pioneer-only players
        os.makedirs(os.path.join(self.stick, V.ENGINE_DIR))
        self.assertEqual(self.main(P.UNIVERSAL)[1].level, V.FAIL)
        self.assertEqual(self.main(P.ANY_DENON)[1].level, V.PASS)

    def test_main_stick_has_tonights_playlists(self):
        real = V.pdb.read

        def with_playlists(path):
            export = real(path)
            export.playlists = {"01 Warmup": [], "Peak": []}
            return export
        with mock.patch.object(V.pdb, "read", with_playlists):
            c = self.main(playlists=["01 Warmup", "02 Peak"])[1]
            self.assertEqual(c.level, V.PASS)
            self.assertIn("tonight's 2 playlists are in its rekordbox library", c.summary)
            c = self.main(playlists=["01 Warmup", "02 Closing"])[1]
            self.assertEqual(c.level, V.WARN)
            self.assertEqual(c.details, ["not in the library: 02 Closing"])
            c = self.main(playlists=["01 Closing"])[1]
            self.assertEqual(c.level, V.FAIL)
            self.assertIn("Is it an older export?", c.summary)

    def test_a_backup_check_doesnt_ask_for_a_library(self):
        shutil.rmtree(os.path.join(self.stick, "PIONEER"))
        r = self.run_check(P.EVERY, role=V.BACKUP)
        self.assertNotIn("Main stick", [c.title for c in r.checks])
        self.assertTrue(r.ready, "\n".join(r.lines()))
        self.assertEqual(r.calm_verdict, "GIG READY")

    def test_device_library_only_is_not_enough_for_every_player(self):
        os.remove(self.one_library)
        c = self.check(self.run_check(), "rekordbox")
        self.assertEqual(c.level, V.FAIL)
        self.assertIn("No OneLibrary", c.details[0])
        self.assertIn("7.2.11", c.details[0])
        # ...but it's all a CDJ-2000 reads
        self.assertEqual(self.level(self.run_check(P.get("cdj-2000")), "rekordbox"), V.PASS)

    def test_onelibrary_only_is_not_enough_for_older_players(self):
        os.remove(os.path.join(self.stick, pdb.RELATIVE))
        c = self.check(self.run_check(), "rekordbox")
        self.assertEqual(c.level, V.FAIL)
        self.assertTrue(any("No Device Library" in d for d in c.details))
        # a CDJ-3000X reads only OneLibrary, whose contents can't be checked
        c = self.check(self.run_check(P.get("cdj-3000x")), "rekordbox")
        self.assertEqual(c.level, V.WARN)
        self.assertIn("encrypted", c.summary)

    def test_a_track_missing_from_the_export(self):
        os.remove(os.path.join(self.stick, "Contents", "Loopmasters", "UnknownAlbum",
                               "Demo Track 2.mp3"))
        r = self.run_check()
        self.assertFalse(r.ready)
        c = self.check(r, "rekordbox")
        self.assertEqual(c.level, V.FAIL)
        self.assertIn("Demo Track 2.mp3", c.details[0])
        self.assertTrue(r.verdict.startswith("NOT READY"))

    def test_a_track_changed_after_export(self):
        p = os.path.join(self.stick, "Contents", "Loopmasters", "UnknownAlbum", "Demo Track 1.mp3")
        mp3(p, 6899624 + 512)
        self.assertEqual(self.level(self.run_check(), "rekordbox"), V.WARN)

    def test_pioneer_folder_without_export(self):
        os.remove(os.path.join(self.stick, pdb.RELATIVE))
        self.assertEqual(self.level(self.run_check(), "rekordbox"), V.FAIL)

    def test_damaged_export(self):
        with open(os.path.join(self.stick, pdb.RELATIVE), "r+b") as fh:
            fh.truncate(3000)
        c = self.check(self.run_check(), "rekordbox")
        self.assertEqual(c.level, V.FAIL)
        self.assertIn("can't be read", c.summary)

    def test_no_export_is_fine_for_a_folder_stick(self):
        shutil.rmtree(os.path.join(self.stick, "PIONEER"))
        self.assertEqual(self.level(self.run_check(), "rekordbox"), V.PASS)

    def test_flac_fails_for_any_player_but_not_for_a_cdj_3000(self):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1",
                        "-c:a", "flac", os.path.join(self.stick, "extra.flac")], check=True)
        c = self.check(self.run_check(), "Formats")
        self.assertEqual(c.level, V.FAIL)
        self.assertIn("extra.flac", c.details[0])
        self.assertEqual(self.level(self.run_check(P.get("cdj-3000")), "Formats"), V.PASS)

    def test_corrupt_audio_fails_the_decode_test(self):
        p = mp3(os.path.join(self.stick, "Sets", "broken.mp3"), seconds=4)
        with open(p, "r+b") as fh:
            fh.seek(os.path.getsize(p) // 2)
            fh.write(os.urandom(4000))
        r = self.run_check()
        c = self.check(r, "Decode test")
        self.assertEqual(c.level, V.FAIL)
        self.assertIn("broken.mp3", c.details[0])

    def test_quick_check_skips_decoding_and_says_so(self):
        r = self.run_check(decode=False)
        self.assertEqual(self.level(r, "Decode test"), V.WARN)
        self.assertTrue(r.ready)

    def test_appledouble_files_warn_and_arent_treated_as_tracks(self):
        with open(os.path.join(self.stick, "._extra.mp3"), "wb") as fh:
            fh.write(b"\0\x05\x16\x07" + b"\0" * 100)
        r = self.run_check()
        self.assertEqual(self.level(r, "Names and sizes"), V.WARN)
        self.assertEqual(self.level(r, "Decode test"), V.PASS)
        self.assertTrue(r.ready)

    def test_too_deep_for_players(self):
        deep = os.path.join(self.stick, *[f"f{i}" for i in range(9)], "deep.mp3")
        mp3(deep)
        c = self.check(self.run_check(), "Names and sizes")
        self.assertEqual(c.level, V.FAIL)
        self.assertIn("more than 8 folders deep", c.details[0])
        # Denon states no depth limit, so nothing is claimed either way
        c = self.check(self.run_check(P.ANY_DENON), "Names and sizes")
        self.assertEqual(c.level, V.PASS)
        self.assertNotIn("deeper", c.summary)

    def test_checksums_against_originals(self):
        originals = self.path("originals")
        os.makedirs(originals)
        on = os.path.join(self.stick, "Contents", "Loopmasters", "UnknownAlbum", "Demo Track 1.mp3")
        shutil.copy(on, os.path.join(originals, "Demo Track 1.mp3"))
        r = self.run_check(sources=[os.path.join(originals, "Demo Track 1.mp3")])
        self.assertEqual(self.level(r, "Checksums"), V.PASS)
        with open(on, "r+b") as fh:
            fh.seek(-10, 2)
            fh.write(b"0123456789")
        c = self.check(V.verify(self.stick, vol=GOOD, decode=False,
                                sources={"Contents/Loopmasters/UnknownAlbum/Demo Track 1.mp3":
                                         os.path.join(originals, "Demo Track 1.mp3")}),
                       "Checksums")
        self.assertEqual(c.level, V.FAIL)

    def test_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            V.main([self.stick, "--quick"])
            os.remove(os.path.join(self.stick, pdb.RELATIVE))
            code = V.main([self.stick, "--quick"])
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", out.getvalue())


class StickCheck(unittest.TestCase):
    def level(self, vol, profile=P.UNIVERSAL):
        return V.check_stick(profile, vol)

    def test_exfat_is_not_ready_for_any_player(self):
        c = self.level(Volume("/s", P.EXFAT, disks.MBR, 1))
        self.assertEqual(c.level, V.FAIL)
        self.assertIn("exFAT", c.details[0])

    def test_mac_guid_stick_with_an_efi_partition(self):
        c = self.level(Volume("/s", P.FAT32, disks.GPT, 2))
        self.assertEqual(c.level, V.FAIL)
        self.assertTrue(any("EFI" in d for d in c.details))

    def test_guid_single_partition_warns(self):
        self.assertEqual(self.level(Volume("/s", P.FAT32, disks.GPT, 1)).level, V.WARN)

    def test_unknown_is_a_warning_not_a_pass(self):
        self.assertEqual(self.level(Volume("/s", error="no tool")).level, V.WARN)
        self.assertEqual(self.level(Volume("/s", disks.FAT, disks.MBR, 1)).level, V.WARN)

    def test_good_stick(self):
        c = self.level(GOOD)
        self.assertEqual((c.level, c.summary), (V.PASS, "FAT32, MBR, one partition"))


class Names(unittest.TestCase):
    def test_fat32_name_rules(self):
        self.assertEqual(V.name_problems("Warmup/Track.mp3"), [])
        self.assertTrue(V.name_problems('Warmup/What? "Edit".mp3'))
        self.assertTrue(V.name_problems("Warmup./Track.mp3"))
        self.assertTrue(V.name_problems("CON.mp3"))
        self.assertTrue(V.name_problems("x" * 256 + ".mp3"))

    def test_slash_in_a_tag_names_the_tag_once(self):
        entries = [V.Entry(f"Contents/OFF / GRID/Downtown Dubs/{n}.mp3", "/nowhere", 1)
                   for n in ("01", "02")]
        entries.append(V.Entry("Contents/ogtrues/hide & seek / devils romance/01.mp3",
                               "/nowhere", 1))
        c = V.check_names(entries, P.UNIVERSAL)
        self.assertEqual((c.level, c.summary), (V.FAIL, "2 problems."))
        self.assertIn("the artist “OFF / GRID” has a “/”", c.details[0])
        self.assertIn("Change the artist to “OFF - GRID”", c.details[0])
        self.assertIn("Change the album to “hide & seek - devils romance”", c.details[1])
        self.assertIsNone(V.slash_split("Contents/Artist/Album/01.mp3"))

    def test_over_4_gb(self):
        big = V.Entry("Mixes/set.wav", "/nowhere", 5 * 1024 ** 3)
        c = V.check_names([big], P.UNIVERSAL)
        self.assertEqual(c.level, V.FAIL)
        self.assertIn("over 4 GB", c.details[0])


class ReportText(unittest.TestCase):
    def test_one_screen_with_a_verdict(self):
        r = V.Report("/Volumes/GIG", P.UNIVERSAL)
        r.checks = [V.Check("Stick", V.PASS, "FAT32, MBR, one partition"),
                    V.Check("Formats", V.FAIL, "6 of 10 files won't play.",
                            [f"t{i}.flac: …" for i in range(6)])]
        lines = r.lines()
        self.assertEqual(lines[0], "Checking /Volumes/GIG for every Pioneer player")
        self.assertIn("        …and 2 more", lines)
        self.assertEqual(lines[-1], "NOT READY: 1 problem to fix.")
        self.assertTrue(r.lines(problems_first=True)[2].startswith("FAIL"))


class DiskTools(unittest.TestCase):
    """Parsing each platform's tool. lsblk's layout is taken from real output;
    the macOS and Windows samples follow the tools' documented keys."""

    def test_lsblk_usb_stick(self):
        data = {"blockdevices": [
            {"name": "sda", "path": "/dev/sda", "fstype": None, "fsver": None, "pttype": "gpt",
             "mountpoints": [None], "rm": False, "hotplug": False, "size": 500107862016,
             "type": "disk", "label": None, "tran": "sata", "children": [
                 {"name": "sda1", "path": "/dev/sda1", "fstype": "ext4", "fsver": "1.0",
                  "pttype": "gpt", "mountpoints": ["/"], "rm": False, "hotplug": False,
                  "size": 500106813440, "type": "part", "label": None, "tran": None}]},
            {"name": "sdb", "path": "/dev/sdb", "fstype": None, "fsver": None, "pttype": "dos",
             "mountpoints": [None], "rm": True, "hotplug": True, "size": 32010928128,
             "type": "disk", "label": None, "tran": "usb", "children": [
                 {"name": "sdb1", "path": "/dev/sdb1", "fstype": "vfat", "fsver": "FAT32",
                  "pttype": "dos", "mountpoints": ["/media/dj/GIG"], "rm": True,
                  "hotplug": True, "size": 32009879552, "type": "part", "label": "GIG",
                  "tran": None}]}]}
        v = disks.parse_lsblk(data, "/media/dj/GIG")
        self.assertEqual((v.file_system, v.scheme, v.partitions, v.disk, v.name, v.removable),
                         (P.FAT32, disks.MBR, 1, "/dev/sdb", "GIG", True))
        self.assertTrue(disks.parse_lsblk(data, "/media/dj/OTHER").error)

    def test_lsblk_older_mountpoint_and_exfat(self):
        data = {"blockdevices": [{"name": "sdc", "pttype": "gpt", "rm": True, "type": "disk",
                                  "children": [
            {"name": "sdc1", "fstype": "vfat", "fsver": "FAT32", "type": "part",
             "mountpoint": None},
            {"name": "sdc2", "fstype": "exfat", "fsver": "1.0", "type": "part",
             "mountpoint": "/media/dj/X"}]}]}
        v = disks.parse_lsblk(data, "/media/dj/X")
        self.assertEqual((v.file_system, v.scheme, v.partitions), (P.EXFAT, disks.GPT, 2))

    def test_diskutil(self):
        vol = {"FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT32",
               "DeviceIdentifier": "disk4s1", "ParentWholeDisk": "disk4", "VolumeName": "GIG"}
        disk = {"Content": "FDisk_partition_scheme", "Internal": False, "RemovableMedia": True,
                "TotalSize": 32010928128}
        listing = {"AllDisksAndPartitions": [
            {"DeviceIdentifier": "disk4", "Partitions": [{"DeviceIdentifier": "disk4s1"}]}]}
        v = disks.parse_diskutil(vol, disk, listing, "/Volumes/GIG")
        self.assertEqual((v.file_system, v.scheme, v.partitions, v.internal),
                         (P.FAT32, disks.MBR, 1, False))
        guid = dict(disk, Content="GUID_partition_scheme")
        two = {"AllDisksAndPartitions": [{"DeviceIdentifier": "disk4", "Partitions": [
            {"DeviceIdentifier": "disk4s1"}, {"DeviceIdentifier": "disk4s2"}]}]}
        v = disks.parse_diskutil(dict(vol, FilesystemName="ExFAT"), guid, two, "/Volumes/GIG")
        self.assertEqual((v.file_system, v.scheme, v.partitions), (P.EXFAT, disks.GPT, 2))

    def test_diskutil_is_asked_the_way_it_answers(self):
        # -plist straight after the verb; at the end diskutil prints its usage and
        # the stick check silently learned nothing about the disk.
        seen = []
        def run(argv):
            seen.append(argv[1:])
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        with mock.patch.object(disks.ffmpeg, "run", run):
            disks._mac("/Volumes/GIG")
        self.assertEqual(seen, [["info", "-plist", "/Volumes/GIG"]])

    def test_windows(self):
        v = disks.parse_windows({"FileSystem": "FAT32", "Label": "GIG", "PartitionStyle": "MBR",
                                 "Partitions": 1, "BusType": "USB", "Size": 32010928128,
                                 "IsSystem": False, "IsBoot": False, "Disk": 2}, "E:\\")
        self.assertEqual((v.file_system, v.scheme, v.partitions, v.removable, v.internal),
                         (P.FAT32, disks.MBR, 1, True, False))
        self.assertEqual(disks.parse_windows({"FileSystem": "FAT"}, "F:\\").file_system, P.FAT16)

    def test_file_system_names(self):
        for raw, ver, want in (("vfat", "FAT32", P.FAT32), ("vfat", "FAT16", P.FAT16),
                               ("vfat", None, disks.FAT), ("MS-DOS FAT32", None, P.FAT32),
                               ("ExFAT", None, P.EXFAT), ("hfsplus", None, P.HFS),
                               ("Mac OS Extended (Journaled)", None, P.HFS),
                               ("ntfs", None, P.NTFS), ("APFS", None, "APFS")):
            self.assertEqual(disks.normal_fs(raw, ver), want, raw)


if __name__ == "__main__":
    unittest.main()
